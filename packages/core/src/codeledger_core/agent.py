## 总体分析

# 这是 **CodeLedger 单 Agent 的核心调度骨架（W2-2）**，实现了经典的「观察 → 决策 → 行动」智能体主循环（Agent Loop），是整个智能体的运行心脏。
# 它的核心设计思路是**先搭骨架、再装大脑**：现阶段不接入真实大模型，先把循环逻辑、边界控制、工具调度、链路追踪全部跑通、测稳；后续只需要替换策略实现，就能无缝接入 LLM 大模型，主循环代码完全不用改动。


# 整个模块四层架构：

# 1. **动作契约层**：定义 Agent 所有合法动作的枚举与标准动作结构，白名单管控，防止越权
# 2. **配置与结果层**：定义运行参数配置、运行结果标准结构，作为循环的输入输出契约
# 3. **策略抽象层**：定义决策接口协议，提供脚本化策略的测试实现，解耦「决策逻辑」和「循环调度」
# 4. **核心循环层**：`AgentLoop` 主类，串联仓库地图、策略决策、工具执行、链路追踪，处理正常结束、失败、步数超限、异常等所有边界场景

# 核心设计亮点：

# - 依赖倒置：通过 Policy 协议解耦决策逻辑，脚本策略和 LLM 策略可以无缝替换
# - 边界完备：最大步数、最大工具调用数双重限流，防止无限循环、成本失控
# - 全链路追踪：无缝对接 TraceRecorder，每一步、每个工具调用都自动埋点
# - 可测试性：不依赖真实模型、不依赖真实沙箱，纯单元测试就能覆盖所有核心路径


# W2-4 是在 Agent 主循环基础上新增的**自动故障恢复机制**，解决的核心痛点是：原来工具执行失败（比如测试跑挂、补丁打不上）后，需要 Policy 额外花一步决策去重新定位问题，既浪费步数、又消耗 token，属于低价值的重复决策。

# 本次升级加入 `RecoveryPlanner` 恢复规划器，在工具执行失败后，**自动执行一步定位动作**（提取错误里的文件路径并读取、搜索失败关键词、重读目标文件），把恢复后的最新观察信息再交给 Policy 继续决策，不用消耗 Policy 的决策轮次，提升修复效率。同时新增修复次数上限，防止无限重试陷入死循环。


from __future__ import annotations
import time
import uuid
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Protocol

from codeledger_core.repo_map import RepoMapBuilder
from codeledger_core.schemas import BaseContract
from codeledger_core.tools import ToolResult
from codeledger_core.trace import (
    TraceEventType,
    TraceRecorder,
    TracedToolRegistry,
    ToolRegistryLike,
)

import json
from pydantic import Field

# 新增正则表达式模块导入，用于从测试失败的错误输出中提取 Python 文件路径，是恢复规划器提取错误信息的核心依赖。
import re



# 二、新增停止原因枚举 StopReason
class StopReason(StrEnum):
    FINISHED = "finished"
    FAILED = "failed"
    MAX_STEPS = "max_steps"
    MAX_TOOL_CALLS = "max_tool_calls"
    MAX_TOKENS = "max_tokens"
    TIME_LIMIT = "time_limit"
    REPEATED_ACTION = "repeated_action"
    ERROR = "error"
    REPAIR_ATTEMPTS_EXCEEDED = "repair_attempts_exceeded"



# 段 2：动作类型枚举 `ActionType`
# - 继承 `StrEnum`，值本身就是字符串，和工具注册器的工具名一一对应，直接可以用来调用工具。
# - 前 5 个是**工具类动作**，和 ToolRegistry 里的 5 个工具完全对应：搜索代码、读文件、打补丁、跑测试、看变更。
# - `FINISH`：终止动作，Agent 认为任务完成，主动结束循环。
# - `FAIL`：终止动作，Agent 认为无法完成任务，主动失败退出。

# > 设计价值：动作白名单机制。Agent 只能在固定动作里选择，不能发明不存在的工具，从接口层面限制能力边界，同时保证所有动作都可追踪、可审计。
class ActionType(StrEnum):
    SEARCH_CODE = "search_code"
    READ_FILE = "read_file"
    APPLY_PATCH = "apply_patch"
    RUN_TESTS = "run_tests"
    GIT_DIFF = "git_diff"
    FINISH = "finish"
    FAIL = "fail"


# 段 3：标准动作结构 `AgentAction`
# - 继承 `BaseContract`，自动获得严格的数据校验，禁止多余字段。
# - `action: ActionType`：动作类型，决定本次要执行什么操作。
# - `arguments: dict = {}`：动作参数字典，对应工具的调用参数，比如 `path`、`query`、`old_text` 等。
# - `reason: str | None = None`：决策理由，可选。后面接入 LLM 时用来存放模型的思考过程，方便追溯决策逻辑。

# > 这是 Policy 输出的标准格式，也是循环和策略之间的数据契约。无论策略是脚本还是大模型，输出都必须符合这个结构。
class AgentAction(BaseContract):
    action: ActionType

    # `arguments: dict = Field(default_factory=dict)`：用 `default_factory=dict` 替代原有的 `{}` 默认值，每个实例都会生成独立的字典，避免可变默认值的副作用
    arguments: dict = Field(default_factory=dict)
    reason: str | None = None
    # `tokens: int = 0`：新增 token 消耗字段，记录本次决策消耗的 token 数量。脚本策略下默认为 0，后续接入 LLM 策略时会填入真实的 token 消耗，用于累计预算控制。
    tokens: int = 0

    # - `signature()` 方法：生成动作的稳定指纹字符串，用于重复动作检测。
    # - 格式为「动作名：排序后的参数字典 JSON」，`sort_keys=True` 保证参数键顺序不同但内容相同的动作，指纹完全一致，不会因为键的顺序不同被误判为不同动作。
    # - `default=str` 兜底，遇到不可序列化的类型也能转成字符串，不会抛出异常。
    # > 核心作用：用来判断 Agent 是否在反复执行完全相同的操作，比如反复读同一个文件、反复搜同一个关键词，出现卡壳时及时终止，避免浪费时间和成本。
    def signature(self) -> str:
        return (
            f"{self.action.value}:"
            f"{json.dumps(self.arguments, sort_keys=True, default=str)}"
        )

# 段 4：运行配置类 `AgentConfig`
# - 冻结数据类，配置不可篡改，保证运行过程中预算规则不被改动。
# - `max_steps: int = 20`：最大步数，默认 20 步。每一轮决策 + 行动算一步，防止无限循环。
# - `max_tool_calls: int = 20`：最大工具调用次数，默认 20 次。单独统计工具调用，防止单步内多次调用工具、成本失控。
# 新增 `max_repair_attempts` 字段，默认值 2，即最多允许自动恢复 2 次。作用：限制自动恢复的次数，防止工具反复失败、恢复动作反复执行，陷入死循环消耗资源。

# > 这是 W2-3 预算控制的基础，两个维度双重限流，是 Agent 安全运行的重要保障。
@dataclass(frozen=True)
class AgentConfig:
    max_steps: int = 20
    max_tool_calls: int = 20
    max_tokens: int = 100_000
    max_wall_time_seconds: float = 600.0
    max_repeated_actions: int = 2
    max_repair_attempts:int=2
    finish_on_test_pass: bool = True



# 段 5：运行结果类 `AgentRunResult`
# - 冻结数据类，是 Agent 循环的统一返回值，所有退出路径都返回这个结构。
# - `success: bool`：任务是否成功完成。
# - `final_output: str`：成功时的最终交付内容，比如修复方案、补丁说明。
# - `steps: int`：实际执行的总步数。
# - `tool_calls: int`：实际调用工具的总次数。
# - `trace_id: str | None = None`：对应的追踪链路 ID，开启追踪时填充，方便回溯完整运行过程。
# - `error: str | None = None`：失败时的错误原因。
# 新增 `tokens_used: int = 0`：任务累计消耗的 token 总数。
# 新增 `elapsed_ms: int = 0`：任务总耗时，单位毫秒。
# 新增 `stop_reason: StopReason | None = None`：结构化停止原因，所有退出场景都会填充对应的枚举值，成功失败都有明确标识。

# 设计价值：返回结果从「成功 / 失败 + 错误信息」升级为「结构化的全量统计信息」，上层可以直接拿到成本、耗时、停止原因，用于统计、对账、效果评估。
@dataclass(frozen=True)
class AgentRunResult:
    success: bool
    final_output: str
    steps: int
    tool_calls: int
    trace_id: str | None = None
    error: str | None = None
    tokens_used: int = 0
    elapsed_ms: int = 0
    stop_reason: StopReason | None = None
    repair_attempts: int = 0


# 段 6：策略协议 `Policy`
# - 结构子类型协议，定义决策能力的接口标准：只要实现了 `decide` 方法，就符合策略要求。
# - 输入：任务描述、当前观察信息、历史摘要列表。
# - 输出：标准 `AgentAction` 动作。
# - 设计思想：**依赖倒置**。主循环不依赖任何具体的决策实现，只依赖这个抽象接口。
#   - 现在用 `ScriptedPolicy` 脚本策略做测试
#   - 后面换成 `LLMPolicy` 大模型策略，主循环代码一行都不用改
class Policy(Protocol):
    def decide(
        self,
        task:str,
        observation:str,
        history:list[str],
    )->AgentAction:
        ...


# 段 7：脚本化策略 `ScriptedPolicy`
# - 这是专门用于测试的「假策略」，按预设的动作列表顺序返回动作，不做任何智能决策。
# - 初始化时传入固定的动作序列，内部维护一个指针下标。
# - 每次调用 `decide` 就按顺序返回下一个动作，指针后移。
# - 动作用完之后，返回 FAIL 动作，原因是策略耗尽。

# > 作用：在没有大模型的情况下，也能完整跑通 Agent 循环的所有路径，用来验证循环逻辑、边界控制、追踪埋点是否正确，是单元测试的核心支撑。
class ScriptedPolicy:
    def __init__(self,actions:list[AgentAction])->None:
        self._actions=list(actions)
        self._index=0

    def decide(
        self,
        task:str,
        observation:str,
        history:list[str],
    )->AgentAction:
        if self._index >=len(self._actions):
            return AgentAction(
                action=ActionType.FAIL,
                arguments={"reason":"scripted policy exhausted"}
            )
        action=self._actions[self._index]
        self._index+=1
        return action


# 五、核心新增：RecoveryPlanner 恢复规划器
# 这是 W2-4 的核心新增类，负责根据失败的动作和结果，生成对应的恢复动作。
class RecoveryPlanner:
    def plan(
        self,
        failed_action:AgentAction,
        result:ToolResult,
    )->AgentAction | None:
        text=f"{result.error or ''}\n{result.output or ''}"
        # - 针对 `RUN_TESTS` 测试失败的场景，做两级恢复策略：
        # 1. **优先精准定位**：用正则从错误输出里匹配 `.py` 文件路径，匹配成功就直接生成「读取该文件」的恢复动作，让 Agent 直接看到报错的文件内容。
        # 2. **降级模糊搜索**：提取不到文件路径时，生成「搜索 FAILED 关键词」的恢复动作，全局定位失败相关代码。
        # - 都附带 `reason` 字段，说明恢复动作的原因，方便 trace 追溯。
        if failed_action.action==ActionType.RUN_TESTS:
            match=re.search(r"([A-Za-z0-9_./\\-]+\.py)",text)
            if match:
                return AgentAction(
                    action=ActionType.READ_FILE,
                    arguments={"path":match.group(1)},
                    reason="test failed; re-localize failing file",
                )
            return AgentAction(
                action=ActionType.SEARCH_CODE,
                arguments={"query":"FAILED"},
                reason="test failed; search failure-related code"
            )
        # - 针对 `APPLY_PATCH` 补丁应用失败的场景：直接从失败动作的参数里取出目标文件路径，生成「重读目标文件」的恢复动作。
        # - 补丁失败通常是因为文件内容和预期不一致，重读最新内容可以让 Policy 拿到准确的上下文重新生成补丁。
        if failed_action.action==ActionType.APPLY_PATCH:
            path=failed_action.arguments.get("path")
            if path:
                return AgentAction(
                    action=ActionType.READ_FILE,
                    arguments={"path":path},
                    reason="patch failed; re-read target file"
                )

        # 其他类型的工具失败（比如搜索、读文件、git diff），没有预设的恢复策略，返回 None 表示无法自动恢复。
        return None
        


# 段 8：核心循环类 `AgentLoop`
class AgentLoop:
    def __init__(self,config:AgentConfig | None = None,recovery: RecoveryPlanner | None = None,)->None:
        self.config=config or AgentConfig()
        self.recovery=recovery or RecoveryPlanner()

    # 8.2 核心运行方法 `run()` — 初始化阶段
    # 方法入参：任务描述、工作目录、策略实例、工具注册器、可选追踪器。
    def run(
        self,
        task:str,
        workspace:str | Path,
        policy:Policy,
        registry:ToolRegistryLike,
        trace:TraceRecorder | None = None,
    )->AgentRunResult:
        # - `started_at = time.perf_counter()`：记录任务开始的高精度时间戳，用于计算总耗时和时间预算检查。
        # - `tokens_used = 0`：初始化累计 token 消耗计数器。
        # - `last_action_signature` / `consecutive_action_count`：只统计连续重复动作。
        #   这样“改代码后再跑一次同样的测试”不会被误判为死循环。
        started_at = time.perf_counter()
        tokens_used = 0
        last_action_signature: str | None = None
        consecutive_action_count = 0

        
        # 第一步：解析工作目录绝对路径。
        workspace_path=Path(workspace).resolve()
        # 第二步：生成仓库地图，作为 Agent 的初始观察信息。Agent 第一眼看到的就是仓库结构，而不是全部代码，符合人类排查问题的逻辑。
        repo_map=RepoMapBuilder().build(workspace_path)
        # 第三步：组装初始观察文本 = 仓库地图 + 任务描述。
        observation=f"{repo_map.render()}\n\nTASK:\n{task}"

        # 第四步：初始化运行状态：
        # history：历史摘要列表，只存简短的动作结果，不存完整输出，避免上下文快速膨胀
        # steps：步数计数器
        # tool_calls：工具调用计数器
        # tools：如果传了追踪器，就把工具注册器包装成带自动埋点的版本；否则直接用原注册器，零侵入
        # 初始化修复次数计数器为 0，每次触发自动恢复就累加 1，用于和上限比对。
        history:list[str]=[]
        steps=0
        tool_calls=0
        tools=TracedToolRegistry(registry,trace) if trace else registry
        repair_attempts = 0

        
        #  第五步：开启追踪的话，记录「任务开始」事件。
        if trace:
            trace.record(
                TraceEventType.RUN_STARTED,
                data={"task":task,"workspace":str(workspace_path)}
            )

        # 8.3 主循环体
        # 外层 try 包裹整个循环，做全局异常兜底。
        # while 循环，条件是步数未达上限。
        # 每轮循环步数先自增。
        # 开启追踪则记录「步骤开始」事件。
        # 调用策略的 decide 方法，传入任务、当前观察、历史摘要，得到下一步动作。这是整个循环的「决策」环节。
        try:
            while steps < self.config.max_steps:
                steps+=1

                # 6.2 每轮循环开头：时间预算检查
                elapsed=time.perf_counter()-started_at
                if elapsed >=self.config.max_wall_time_seconds:
                    reason="wall time limit reached"
                    if trace:
                        trace.record(
                            TraceEventType.ERROR,
                            step_index=steps,
                            error=reason,
                        )
                    return AgentRunResult(
                        success=False,
                        final_output="",
                        steps=steps,
                        tool_calls=tool_calls,
                        trace_id=trace.trace_id if trace else None,
                        error=reason,
                        tokens_used=tokens_used,
                        elapsed_ms=int(elapsed*1000),
                        stop_reason=StopReason.TIME_LIMIT,
                        repair_attempts=repair_attempts
                    )

                if trace:
                    trace.record(
                        TraceEventType.STEP_STARTED,
                        step_index=steps,
                    )

                action=policy.decide(task,observation,history)
                # 6.3 决策后：token 预算 + 重复动作检查
                # - 先把本次决策的 token 消耗累加到总计数器。
                # - 超过上限则立刻终止，停止原因标记为 `MAX_TOKENS`。
                # - 返回结果里带上累计 token 数和当前耗时。
                tokens_used+=action.tokens
                if tokens_used > self.config.max_tokens:
                    reason="max tokens reached"
                    if trace:
                        trace.record(
                            TraceEventType.ERROR,
                            step_index=steps,
                            error=reason,
                        )
                    return AgentRunResult(
                        success=False,
                        final_output="",
                        steps=steps,
                        tool_calls=tool_calls,
                        trace_id=trace.trace_id if trace else None,
                        error=reason,
                        tokens_used=tokens_used,
                        elapsed_ms=int((time.perf_counter() - started_at) * 1000),
                        stop_reason=StopReason.MAX_TOKENS,
                        repair_attempts=repair_attempts
                    )

                # 重复动作检查
                # - 生成当前动作的指纹。
                # - 在计数字典里对应计数 +1。
                # - 如果超过最大重复次数，立刻终止，停止原因标记为 `REPEATED_ACTION`。
                # 设计作用：Agent 有时候会陷入「读文件 → 没看懂 → 再读一遍」的死循环，重复动作检测能及时终止这种无意义的循环，避免浪费时间和成本。
                signature = action.signature()
                if signature == last_action_signature:
                    consecutive_action_count += 1
                else:
                    last_action_signature = signature
                    consecutive_action_count = 1

                if consecutive_action_count > self.config.max_repeated_actions:
                    reason = "repeated action limit reached"
                    if trace:
                        trace.record(
                            TraceEventType.ERROR,
                            step_index=steps,
                            error=reason,
                        )
                    return AgentRunResult(
                        success=False,
                        final_output="",
                        steps=steps,
                        tool_calls=tool_calls,
                        trace_id=trace.trace_id if trace else None,
                        error=reason,
                        tokens_used=tokens_used,
                        elapsed_ms=int((time.perf_counter() - started_at) * 1000),
                        stop_reason=StopReason.REPEATED_ACTION,
                        repair_attempts=repair_attempts
                    )

                # 8.4 终止动作判断：FINISH
                if action.action==ActionType.FINISH:
                    final_output=str(action.arguments.get("answer",""))
                    if trace:
                        trace.record(
                            TraceEventType.RUN_FINISHED,
                            step_index=steps,
                            data={"success":True,"final_output":final_output},
                        )

                    return AgentRunResult(
                        success=True,
                        final_output=final_output,
                        steps=steps,
                        tool_calls=tool_calls,
                        trace_id=trace.trace_id if trace else None,
                        error=None,
                        tokens_used=tokens_used,
                        elapsed_ms=int((time.perf_counter()-started_at)*1000),
                        stop_reason=StopReason.FINISHED,
                        repair_attempts=repair_attempts
                    )

                # 8.5 终止动作判断：FAIL
                if action.action == ActionType.FAIL:
                    reason = str(action.arguments.get("reason", "agent failed"))
                    if trace:
                        trace.record(
                            TraceEventType.ERROR,
                            step_index=steps,
                            error=reason,
                        )
                    return AgentRunResult(
                        success=False,
                        final_output="",
                        steps=steps,
                        tool_calls=tool_calls,
                        trace_id=trace.trace_id if trace else None,
                        error=reason,
                        tokens_used=tokens_used,
                        elapsed_ms=int((time.perf_counter() - started_at) * 1000),
                        stop_reason=StopReason.FAILED,
                        repair_attempts=repair_attempts
                    )

                # 8.6 工具调用数超限判断
                # - 在执行工具前先检查：工具调用数是否已经达到上限。
                # - 达到上限直接终止，返回失败，原因是工具调用数超限。
                # - 这是第二道预算闸门，和最大步数形成双重保护，防止 Agent 反复调用工具、成本失控。
                if tool_calls >= self.config.max_tool_calls:
                    reason = "max tool calls reached"
                    if trace:
                        trace.record(
                            TraceEventType.ERROR,
                            step_index=steps,
                            error=reason,
                        )
                    return AgentRunResult(
                        success=False,
                        final_output="",
                        steps=steps,
                        tool_calls=tool_calls,
                        trace_id=trace.trace_id if trace else None,
                        error=reason,
                        tokens_used=tokens_used,
                        elapsed_ms=int((time.perf_counter() - started_at) * 1000),
                        stop_reason=StopReason.MAX_TOOL_CALLS,     
                        repair_attempts=repair_attempts               
                    )

                # 8.7 执行工具调用
                started = time.perf_counter()
                result: ToolResult = tools.call(
                    action.action.value,
                    **action.arguments,
                )
                duration_ms = int((time.perf_counter() - started) * 1000)
                tool_calls += 1


                # - 把工具输出作为下一轮的观察信息，替换掉旧的 observation。每一轮决策只基于最新的观察，控制上下文大小。
                # - 如果工具执行有错误，把错误信息追加到观察里，让策略知道执行出错了。
                # - 向历史列表追加一条**简短摘要**，只记步数、动作、是否成功，不存完整输出。
                # > 设计考量：完整输出放在 observation 里给当前决策用，history 只留摘要用于长期记忆，避免上下文随步数线性膨胀。
                observation = result.output
                if result.error:
                    observation = f"{observation}\nERROR:\n{result.error}"

                history.append(
                    f"step={steps} action={action.action.value} ok={result.ok}"
                )

                # 8.9 记录步骤结束
                if trace:
                    trace.record(
                        TraceEventType.STEP_FINISHED,
                        step_index=steps,
                        duration_ms=duration_ms,
                        data={
                            "action": action.action.value,
                            "ok": result.ok,
                        },
                    )

                # 本地测试全部通过后，直接结束，避免模型反复检查同一个 diff。
                if (
                    self.config.finish_on_test_pass
                    and action.action == ActionType.RUN_TESTS
                    and result.ok
                ):
                    final_output = "tests passed"
                    if trace:
                        trace.record(
                            TraceEventType.RUN_FINISHED,
                            step_index=steps,
                            data={
                                "success": True,
                                "final_output": final_output,
                            },
                        )
                    return AgentRunResult(
                        success=True,
                        final_output=final_output,
                        steps=steps,
                        tool_calls=tool_calls,
                        trace_id=trace.trace_id if trace else None,
                        tokens_used=tokens_used,
                        elapsed_ms=int(
                            (time.perf_counter() - started_at) * 1000
                        ),
                        stop_reason=StopReason.FINISHED,
                        repair_attempts=repair_attempts,
                    )

                # 八、核心改动：工具执行后的自动恢复逻辑
                # 先判断工具执行是否失败。只有失败才会进入恢复流程，成功直接进入下一轮决策。
                if not result.ok:
                    # 修复次数计数器 +1，注意：**一次工具失败对应一次恢复尝试**，不管恢复动作本身成功还是失败。
                    repair_attempts+=1

                    # - 检查修复次数是否超过配置上限。
                    # - 超限则直接终止任务，记录错误追踪事件，返回标准化失败结果，停止原因为 `REPAIR_ATTEMPTS_EXCEEDED`。
                    # - 这是防止无限恢复的安全闸门，避免工具反复失败、恢复动作无限执行。
                    if repair_attempts > self.config.max_repair_attempts:
                        reason = "repair attempts exceeded"
                        if trace:
                            trace.record(
                                TraceEventType.ERROR,
                                step_index=steps,
                                error=reason,
                            )
                        return AgentRunResult(
                            success=False,
                            final_output="",
                            steps=steps,
                            tool_calls=tool_calls,
                            trace_id=trace.trace_id if trace else None,
                            error=reason,
                            tokens_used=tokens_used,
                            elapsed_ms=int(
                                (time.perf_counter() - started_at) * 1000
                            ),
                            stop_reason=StopReason.REPAIR_ATTEMPTS_EXCEEDED,
                            repair_attempts=repair_attempts,
                        )
                    # - 调用恢复规划器生成恢复动作。
                    # - 如果返回 None（没有对应恢复策略），直接终止任务，标记为修复次数超限。
                    # - 设计考量：无法自动恢复的故障，没必要继续消耗资源，直接终止，交给上层处理。
                    recovery_action=self.recovery.plan(action,result)
                    if recovery_action is None:
                        reason = "no recovery action available"
                        if trace:
                            trace.record(
                                TraceEventType.ERROR,
                                step_index=steps,
                                error=reason,
                            )
                        return AgentRunResult(
                            success=False,
                            final_output="",
                            steps=steps,
                            tool_calls=tool_calls,
                            trace_id=trace.trace_id if trace else None,
                            error=reason,
                            tokens_used=tokens_used,
                            elapsed_ms=int(
                                (time.perf_counter() - started_at) * 1000
                            ),
                            stop_reason=StopReason.REPAIR_ATTEMPTS_EXCEEDED,
                            repair_attempts=repair_attempts,
                        )

                    # - 自动执行恢复动作，走的是同一个工具注册器（带追踪的也会自动埋点）。
                    # - 工具调用计数器 +1，恢复动作属于工具调用，计入总调用数预算。
                    # - 关键：**这一步不消耗 Policy 决策步数**，是在当前 step 内自动完成的，相当于一步里做了「失败工具 + 恢复工具」两次工具调用，但只算一步决策。
                    recovery_result=tools.call(
                        recovery_action.action.value,
                        **recovery_action.arguments,
                    )
                    tool_calls+=1

                    # - 用恢复动作的输出更新 observation，作为下一轮 Policy 决策的观察信息。
                    # - 恢复动作如果有错误，也追加到观察里。
                    # - 向历史记录追加一条恢复动作的摘要，标记是恢复操作，和正常决策动作区分开。
                    # > 设计亮点：恢复动作完全在循环内部自动完成，对 Policy 透明。Policy 下一轮拿到的就是已经恢复好的最新观察信息，不用自己决策去定位，节省了一步决策成本。
                    observation=recovery_result.output
                    if recovery_result.error:
                        observation=f"{observation}\nERROR:\n{recovery_result.error}"

                    history.append(
                        f"recovery={recovery_action.action.value} "
                        f"ok={recovery_result.ok}"
                    )

                    

            # 8.10 步数超限退出
            reason = "max steps reached"
            if trace:
                trace.record(
                    TraceEventType.ERROR,
                    step_index=steps,
                    error=reason,
                )
            return AgentRunResult(
                success=False,
                final_output="",
                steps=steps,
                tool_calls=tool_calls,
                trace_id=trace.trace_id if trace else None,
                error=reason,
                tokens_used=tokens_used,
                elapsed_ms=int((time.perf_counter() - started_at) * 1000),
                stop_reason=StopReason.MAX_STEPS,
                repair_attempts=repair_attempts
            )
        # 8.11 全局异常捕获
        # - 捕获循环过程中所有未处理的异常，保证 Agent 不会崩溃。
        # - 记录错误事件，保留崩溃现场。
        # - 返回标准化的失败结果，错误信息为异常内容。

        # > 设计价值：Agent 永远返回结构化结果，永远不会抛出异常崩溃，上层调用非常安全。
        except Exception as exc:
            if trace:
                trace.record(
                    TraceEventType.ERROR,
                    step_index=steps,
                    error=str(exc),
                )
            return AgentRunResult(
                success=False,
                final_output="",
                steps=steps,
                tool_calls=tool_calls,
                trace_id=trace.trace_id if trace else None,
                error=str(exc),
                tokens_used=tokens_used,
                elapsed_ms=int((time.perf_counter() - started_at) * 1000),
                stop_reason=StopReason.ERROR,
                repair_attempts=repair_attempts
            )
                



                


        





