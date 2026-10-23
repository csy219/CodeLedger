# 这是 W2-5A 的核心交付：**大模型决策策略实现（LLMPolicy）**，是 Agent 从「脚本测试桩」升级为「真实智能体」的核心模块。
# 它完整实现了之前定义的 `Policy` 协议，和 `ScriptedPolicy` 接口完全一致，可以无缝替换进 `AgentLoop` 主循环，不用修改主循环一行代码。核心工作链路：接收任务 + 当前观察 + 历史摘要 → 拼接成标准 Prompt → 调用 DeepSeek 大模型 API → 清洗并解析返回的 JSON 动作 → 转成标准 `AgentAction` 结构 → 回填 token 消耗，自动对接主循环的预算管控。

# 整个模块四层架构：

# 1. **契约层**：系统提示词定义动作白名单、执行规则、输出格式，从输入侧约束大模型的行为边界
# 2. **配置层**：`LLMConfig` 封装模型、地址、密钥、超时、重试等参数
# 3. **执行层**：`LLMPolicy` 主类，负责 Prompt 拼接、API 调用、重试控制、结果解析、token 统计
# 4. **容错层**：`_parse_action` 清洗模型输出，兼容 Markdown 代码块、字段别名等常见格式偏差

# 设计特点：

# - 无缝替换：完全符合 Policy 协议，主循环零改动即可从脚本策略切换为真实大模型
# - 格式强约束：系统提示词 + 原生 JSON 模式双重保障，输出可解析率高
# - 容错能力：自动清洗格式、兼容字段别名，降低重试率
# - 预算对接：自动提取 token 消耗填入动作，主循环的 token 预算直接生效
# - 可测试性：支持注入自定义 HTTP 客户端，单元测试不用真实发请求


from __future__ import annotations
import json
import re
from dataclasses import dataclass
from typing import Any, Protocol

# `httpx`：现代同步 HTTP 客户端，用于调用大模型 API，自带超时控制，轻量且性能优于 requests。
import httpx

from codeledger_core.agent import AgentAction


class HTTPClientLike(Protocol):
    def post(
        self,
        url: str,
        *,
        headers: dict[str, str],
        json: dict[str, Any],
    ) -> Any:
        ...



# 这是给大模型的「行为准则 + 操作手册」，是约束模型输出、保证动作合法的第一道闸门。

# - 身份定义：明确角色为代码修复智能体 CodeLedger。
# - 动作白名单：完整列出 7 个允许的动作和对应参数格式，和工具注册器的能力一一对应，从规则层面禁止模型发明新工具。
# - 7 条硬性执行规则：
#   1. 只返回合法 JSON
#   2. 不要输出 Markdown 代码块
#   3. 每次只返回一个动作
#   4. 不能发明不存在的工具
#   5. 修改代码前必须先看错误输出
#   6. 补丁必须使用文件里的精确原文
#   7. 测试通过、补丁验证无误后才能调用 finish
# - 标准 JSON 格式示例：给出明确的输出模板，大幅降低模型输出格式错误的概率。
# - 末尾 `.strip()` 去掉首尾空白，减少不必要的 token 消耗。
SYSTEM_PROMPT = """
You are CodeLedger, a code-repair agent.

You must choose exactly one action.

Allowed actions:
- search_code: {"query": str, "max_results": int}
- read_file: {"path": str, "start_line": int, "end_line": int}
- apply_patch: {"path": str, "old_text": str, "new_text": str}
- run_tests: {"command": str}
- git_diff: {}
- finish: {"answer": str}
- fail: {"reason": str}

Rules:
1. Return ONLY valid JSON.
2. Do not return Markdown code fences.
3. Use one action per response.
4. Do not invent tools.
5. Prefer reading failure output before editing.
6. Use apply_patch only with the exact old_text from the file.
7. When tests pass and the patch is verified, use finish.

JSON format:
{
  "action": "read_file",
  "arguments": {"path": "example.py"},
  "reason": "why this action is needed"
}
""".strip()


# 段 3：大模型配置类 LLMConfig
# - 冻结数据类，配置创建后不可修改，保证运行过程中参数不被意外篡改。
# - `model`：默认使用 DeepSeek 标准版对话模型。
# - `base_url`：DeepSeek 兼容 OpenAI 协议的聊天补全接口地址，通用兼容所有 OpenAI 格式的模型服务。
# - `api_key`：API 密钥，由调用方从环境变量传入，绝不硬编码在代码里，避免密钥泄露。
# - `timeout_seconds`：请求超时时间，默认 60 秒，防止网络波动导致 Agent 永久挂死。
# - `max_retries`：最大重试次数，默认失败后重试 2 次，加上首次调用一共 3 次机会，应对网络抖动、接口限流等临时故障。
@dataclass(frozen=True)
class LLMConfig:
    model: str = "deepseek-chat"
    base_url: str = "https://api.deepseek.com/chat/completions"
    api_key: str | None = None
    timeout_seconds: float = 60.0
    max_retries: int = 2


# 段 4：LLMPolicy 主类构造函数
class LLMPolicy:
    def __init__(
        self,
        config:LLMConfig,
        client:HTTPClientLike | None=None,
    )->None:
        if not config.api_key:
            raise ValueError("LLMConfig.api_key is required")
        self.config=config
        self.client=client or httpx.Client(timeout=config.timeout_seconds)

    # 段 5：核心决策方法 decide ()
    # 方法签名与 Prompt 拼接
    def decide(
        self,
        task:str,
        observation:str,
        history:list[str],
    )->AgentAction:
        # - 方法签名完全符合 `Policy` 协议，输入任务、观察、历史，输出 AgentAction，和 ScriptedPolicy 接口 100% 一致，可以无缝替换。
        # - 拼接用户侧 Prompt，结构清晰分为三个区块：
        # 1. **TASK**：当前要解决的任务描述
        # 2. **OBSERVATION**：最新的观察信息（仓库地图、工具输出、错误信息等）
        # 3. **HISTORY**：历史动作摘要，**只取最近 10 条**，控制上下文长度，避免 token 过快耗尽
        # - 历史为空时显示 `(empty)`，避免空行造成模型语义误解。
        user_prompt = "\n".join(
            [
                f"TASK:\n{task}",
                "",
                f"OBSERVATION:\n{observation}",
                "",
                "HISTORY:",
                "\n".join(history[-10:]) or "(empty)",
            ]
        )

        # 重试循环与 API 调用
        # - 初始化错误变量，用来保存重试过程中最后一次异常。
        # - 循环次数 = 重试次数 + 1（首次调用），比如重试 2 次就一共尝试 3 次。
        # - 发送 POST 请求到 DeepSeek 接口，完全遵循 OpenAI 兼容协议：
        # - 请求头：Bearer 认证、JSON 格式
        # - 请求体：模型名、消息列表（系统提示词 + 用户提示词）
        # - `temperature: 0`：温度设为 0，输出尽可能确定可复现，减少随机性，适配代码修复这类强确定性任务
        # - `response_format: {"type": "json_object"}`：开启模型原生 JSON 输出模式，强制模型返回合法 JSON，大幅降低格式错误率
        # - `raise_for_status()`：HTTP 状态码非 2xx 直接抛异常，进入重试逻辑。
        last_error:Exception | None = None
        for _ in range(self.config.max_retries+1):
            try:
                response=self.client.post(
                    self.config.base_url,
                    headers={
                        "Authorization":f"Bearer {self.config.api_key}",
                        "Content-Type":"application/json",
                    },
                    json={
                        "model":self.config.model,
                        "messages":[
                            {"role":"system","content":SYSTEM_PROMPT},
                            {"role":"user","content":user_prompt},
                        ],
                        "temperature":0,
                        "response_format":{"type":"json_object"},
                    },
                )
                response.raise_for_status()
                # - 解析响应 JSON，取出模型返回的文本内容。
                # - 调用 `_parse_action` 清洗并解析动作文本，得到结构化字典。
                # - 从响应的 `usage` 字段提取本次调用的总 token 消耗，填入动作字典的 `tokens` 字段。
                # - 用 `AgentAction.model_validate()` 做严格校验，生成标准动作对象返回：
                # - 校验不通过会抛出异常，自动进入重试逻辑
                # - 保证返回的一定是合法的 AgentAction，主循环可以直接安全使用
                data=response.json()
                content=data["choices"][0]["message"]["content"]
                action_data=self._parse_action(content)
                usage=data.get("usage")or {}
                action_data["tokens"]=int(usage.get("total_tokens",0))
                return AgentAction.model_validate(action_data)
            
            # - 捕获所有异常（网络错误、解析错误、校验错误等），保存到 last_error，继续下一轮重试。
            # - 所有重试全部用完后仍失败，抛出运行时异常，带上最后一次的错误信息。
            # - 这个异常会被 AgentLoop 的全局异常捕获接住，标记为 ERROR 停止原因，不会导致整个进程崩溃。
            except Exception as exc:
                last_error=exc
        raise RuntimeError(f"LLMPolicy failed after retries: {last_error}")
        



    # 段 6：静态解析方法 _parse_action ()
    # - 静态工具方法，输入模型返回的文本，输出解析后的动作字典。
    # - 第一步：去掉首尾空白字符。
    # - 第二步：正则去掉开头的 `json 或 ` 代码块标记，兼容模型不小心输出 Markdown 代码块的常见情况。
    # - 第三步：正则去掉结尾的 ``` 代码块标记。
    # - 第四步：JSON 反序列化成字典。
    # - 兼容处理：如果返回的字段是 `tool` 而非标准的 `action`，自动重命名为 `action`，兼容模型的字段别名输出，进一步提升解析成功率。
    # - 这一层是「格式容错层」，应对大模型输出的小偏差，减少不必要的重试，提升整体成功率。
    @staticmethod
    def _parse_action(content:str)->dict:
        text=content.strip()
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)

        action_data=json.loads(text)
        if "tool" in action_data and "action" not in action_data:
            action_data["action"]=action_data.pop("tool")
        return action_data




