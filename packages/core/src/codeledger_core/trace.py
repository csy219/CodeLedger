## 总体分析
# 这是 **CodeLedger 项目的链路追踪模块（W1-6）**，核心作用是完整记录 Agent 运行全生命周期的每一个关键事件，
# 实现运行过程可回放、可排查、可统计。它采用 JSONL（每行一条 JSON）的持久化格式，支持增量写入、崩溃不丢数据；
# 同时通过装饰器模式包装工具注册器，实现工具调用的自动埋点，零侵入原有业务逻辑就能获得完整追踪能力。

# 整个模块分为三层架构：
# 1. **事件定义层**：事件类型枚举 + 事件数据模型，统一定义所有可记录事件的标准格式
# 2. **记录核心层**：`TraceRecorder`，负责事件写入、文件持久化、读取解析、统计汇总
# 3. **自动埋点层**：`TracedToolRegistry`，用包装器模式包裹原有工具注册器，自动记录工具调用的开始、结束、耗时、结果

from __future__ import annotations

import json
import time
import uuid
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol

from pydantic import Field

from codeledger_core.schemas import BaseContract, utc_now
from codeledger_core.tools import ToolResult


# 段 2：事件类型枚举 `TraceEventType`
# - 继承 `StrEnum`，所有枚举值都是原生字符串，序列化、存日志、转 JSON 都无需额外转换。
# - `RUN_STARTED / RUN_FINISHED`：整个任务的开始与结束，标记全生命周期的首尾。
# - `STEP_STARTED / STEP_FINISHED`：单个执行步骤的起止，对应 Agent 执行流程中的每个阶段。
# - `MODEL_CALL`：大模型调用事件，记录每次模型请求。
# - `TOOL_CALL_STARTED / TOOL_CALL_FINISHED`：工具调用的开始与结束，成对出现，通过 ID 关联。
# - `ERROR`：错误事件，记录运行中出现的所有异常。

# > 设计价值：所有事件有标准化分类，后续统计、筛选、回放都可以按类型过滤，比如统计工具调用次数、错误数、模型调用次数，支撑数据统计与问题排查。
class TraceEventType(StrEnum):
    RUN_STARTED = "run_started"
    STEP_STARTED = "step_started"
    MODEL_CALL = "model_call"
    TOOL_CALL_STARTED = "tool_call_started"
    TOOL_CALL_FINISHED = "tool_call_finished"
    STEP_FINISHED = "step_finished"
    RUN_FINISHED = "run_finished"
    ERROR = "error"


# 段 3：事件数据模型 `TraceEvent`
# - 继承 `BaseContract`：复用全局契约的严格校验规则（禁止多余字段、支持别名初始化），保证所有事件数据格式合规。
# - `schema_version`：事件结构版本号，未来结构大改时升级，保证可以兼容解析历史追踪文件。
# - `trace_id`：整条追踪链路的唯一标识，一次运行对应一个 trace_id。
# - `run_id`：关联的任务运行 ID，和 schemas 中 `Run.id` 一一对应。
# - `sequence`：自增事件序号，保证事件可以按严格顺序回放，不会乱序。
# - `event_type`：事件类型枚举。
# - `timestamp`：事件发生时间，默认使用全局统一的 UTC 时间生成函数，保证全系统时间标准一致。
# - `step_index`：关联的步骤序号，步骤类事件填充。
# - `tool_call_id`：关联的工具调用 ID，工具类事件填充，开始和结束事件用同一个 ID 配对。
# - `duration_ms`：耗时（毫秒），结束类事件填充。
# - `data`：灵活数据字典，不同事件可以存放不同的上下文数据，比如工具调用放参数、结果，不用为每种事件单独定义模型。
# - `error`：错误信息，错误事件专用。

# > 设计特点：通用核心字段 + 灵活 data 字段，既保证了核心字段统一规范，又支持不同事件的差异化数据，扩展性极强。
class TraceEvent(BaseContract):
    schema_version: str = "1.0"
    trace_id: str
    run_id: str
    sequence: int
    event_type: TraceEventType
    timestamp: datetime = Field(default_factory=utc_now)
    step_index: int | None = None
    tool_call_id: str | None = None
    duration_ms: int | None = None
    data: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None


# 段 4：追踪记录器核心类 `TraceRecorder`
# 这是整个模块的核心，负责事件写入、持久化、读取、汇总全流程。
class TraceRecorder:
    def __init__(
        self,
        # 接收任务 ID、输出目录、可选追踪 ID。
        run_id:str,
        output_dir:str | Path ="runs",
        trace_id:str | None=None,
    )->None:
        self.run_id=run_id 

        # 没传 trace_id 就自动生成，格式为前缀 + uuid 前 12 位，兼顾可读性与唯一性。
        self.trace_id=trace_id or f"trace-{uuid.uuid4().hex[:12]}"

        # 输出目录转为 Path 对象，自动递归创建目录，已存在也不报错。
        self.output_dir=Path(output_dir)
        self.output_dir.mkdir(parents=True,exist_ok=True)

        # 确定输出文件路径：以 trace_id 命名的 `.jsonl` 文件。
        self.path=self.output_dir / f"{self.trace_id}.jsonl"
        # 初始化清空文件，保证每次运行是全新的追踪文件。
        self.path.write_text("",encoding="utf-8")
        # `_sequence = 0`：序号计数器初始化为 0，每次记录事件自增 1。
        self._sequence=0

    # 4.2 事件记录方法 `record`
    # - `*` 强制所有可选参数必须用关键字传参，避免位置传参错误。
    # - 序号计数器自增，事件序号从 1 开始。
    # - 构造 `TraceEvent` 对象，填充所有字段，data 为空则默认空字典。
    # - 以**追加模式**打开 JSONL 文件，指定换行符为 `\n`，保证跨平台格式一致。
    # - 将事件序列化为 JSON 字符串，写入一行并换行。
    # - 返回构造好的事件对象。

    # > 关键设计：每条事件立刻追加落盘。即使进程中途崩溃，已经写入的事件也不会丢失，最大限度保留现场信息。JSONL 每行独立，不会因为最后一行写了一半
    def record(
        self,
        event_type: TraceEventType,
        *,
        step_index: int | None = None,
        tool_call_id: str | None = None,
        duration_ms: int | None = None,
        data: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> TraceEvent:
        self._sequence += 1
        event = TraceEvent(
            trace_id=self.trace_id,
            run_id=self.run_id,
            sequence=self._sequence,
            event_type=event_type,
            step_index=step_index,
            tool_call_id=tool_call_id,
            duration_ms=duration_ms,
            data=data or {},
            error=error,
        )
        with self.path.open("a",encoding="utf-8",newline="\n") as fh:
            fh.write(event.model_dump_json())
            fh.write("\n")
        return event
    
    # 4.3 静态读取方法 `read_events`
    # - 静态方法，传入文件路径即可读取，不需要创建实例。
    # - 逐行读取文件，自动跳过空行。
    # - 每行用 Pydantic 的 `model_validate_json` 反序列化成 `TraceEvent` 对象，自动做格式校验。
    # - 返回按顺序排列的事件列表。
    # > 设计价值：读取与写入解耦，既可以通过实例调用，也可以单独用来解析历史 trace 文件，灵活度高。
    @staticmethod
    def read_events(path:str | Path)->list[TraceEvent]:
        events:list[TraceEvent]=[]
        with Path(path).open("r",encoding="utf-8") as fh:
            for line in fh:
                if line.strip():
                    events.append(TraceEvent.model_validate_json(line))
        return events


    # 4.4 汇总方法 `summary`
    # - 读取当前文件的所有事件。
    # - 统计已完成的工具调用数量、错误事件数量。
    # - 返回汇总字典，包含追踪 ID、任务 ID、总事件数、工具调用数、错误数、文件路径。

    # > 作用：快速概览一次运行的整体情况，不用遍历所有事件，适合列表页、概览页展示。
    def summary(self)->dict[str,Any]:
        events=self.read_events(self.path)
        tool_calls=sum(
            1 for event in events if event.event_type==TraceEventType.TOOL_CALL_FINISHED
        )
        errors=sum(1 for event in events if event.event_type==TraceEventType.ERROR)
        return {
            "trace_id":self.trace_id,
            "run_id":self.run_id,
            "event_count":len(events),
            "tool_calls":tool_calls,
            "errors":errors,
            "path":str(self.path)
        }

# 段 5：工具注册器协议 `ToolRegistryLike`
# - 结构子类型协议，定义工具注册器的接口标准：只要实现了接收工具名 + 参数、返回 `ToolResult` 的 `call` 方法，就符合要求。
# - 设计目的：解耦追踪包装器和具体工具注册器实现。`TracedToolRegistry` 不依赖具体的 `ToolRegistry` 类，只依赖这个接口，符合依赖倒置原则，也方便单元测试用 Fake 对象替换。
class ToolRegistryLike(Protocol):
    def call(self, name: str, **kwargs: Any) -> ToolResult:
        ...



# 段 6：带追踪的工具注册器 `TracedToolRegistry`
# 这是**装饰器模式**的典型实现：在不修改原有 `ToolRegistry` 代码的前提下，
# 给所有工具调用加上自动追踪埋点，对上层完全透明。
class TracedToolRegistry:

    # 构造函数接收被包装的注册器实例和追踪器实例。
    def __init__(
        self,
        registry:ToolRegistryLike,
        trace:TraceRecorder
    )->None:
        self.registry=registry
        self.trace=trace

    # `call` 方法签名和原注册器完全一致，符合 `ToolRegistryLike` 协议，替换后上层代码不用任何修改。
    # 设计亮点：零侵入式埋点。原来怎么用 `ToolRegistry`，现在换成 `TracedToolRegistry` 就行，上层代码一行不用改，自动获得完整的工具调用追踪能力。
    def call(self,name:str,**kwargs:Any)->ToolResult:
        # 生成唯一的 `tool_call_id`，用来配对同一次调用的开始与结束事件
        tool_call_id=f"tool-{uuid.uuid4().hex[:12]}"
        # 记录高精度开始时间，`time.perf_counter()` 适合计算短耗时，精度远高于系统时间。
        started=time.perf_counter()

        # 先记录「工具调用开始」事件，带上工具名和调用参数。
        self.trace.record(
            event_type=TraceEventType.TOOL_CALL_STARTED,
            tool_call_id=tool_call_id,
            data={"tool":name,"arguments":kwargs},
        )

        # 调用原注册器的 `call` 方法，执行业务逻辑，拿到结果。
        result=self.registry.call(name,**kwargs)
        # 计算总耗时，转换为毫秒整数。
        duration_ms=int((time.perf_counter()-started)*1000)

        # 记录「工具调用结束」事件，带上耗时、工具名、成功状态、错误信息、失败分类。
        self.trace.record(
            event_type=TraceEventType.TOOL_CALL_FINISHED,
            tool_call_id=tool_call_id,
            duration_ms=duration_ms,
            data={
                "tool":name,
                "ok":result.ok,
                "error":result.error,
                "failure":result.failure.category.value if result.failure else None,
            }
        )
        # 原样返回工具结果，对上层业务完全无感知。
        return result



    

