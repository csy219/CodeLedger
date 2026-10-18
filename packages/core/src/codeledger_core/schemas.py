# 这是 **CodeLedger / RepoFix 项目的核心数据契约层**，基于 Pydantic 实现，本质是**整个系统各组件之间统一的数据交换标准**。所有模块（测试框架、智能体、运行时、前端 UI）都严格使用这些结构传递数据，保证跨模块交互的格式一致、可序列化、可追溯。
# 下面逐部分完整解析设计与含义。


# 这是模块级说明，明确了核心定位：

# - **数据契约（data contracts）**：相当于系统间的 “通信协议”，只要契约不变，harness（测试执行框架）、agent（智能体）、runtime（运行时）、UI（界面）可以独立开发、迭代，互不影响。
# - **稳定边界**：这些模型是系统的稳定接口，不会轻易改动，保障前后兼容。
# - **两个硬性要求**：必须可序列化（方便跨进程 / 网络传输、存数据库）、必须带版本（结构变更时可以兼容旧数据）。
"""Core data contracts for CodeLedger / RepoFix.

These models are the stable boundary between the harness, agent, runtime and UI.
Keep them serializable and versioned.
"""


# 导入项	作用
# from __future__ import annotations	延迟类型注解求值，支持类内部引用自身类型、前向引用，是复杂 Pydantic 模型的标准写法。
# datetime, timezone	时间类型，全系统强制使用带时区的 UTC 时间，避免时区混乱。
# StrEnum	字符串枚举，枚举值本身就是字符串，序列化 / 反序列化无需转换，天然兼容 JSON。
# Any, Literal	类型提示：Any 表示任意类型；Literal 用来限定字段只能取若干固定字符串值，做编译期类型校验。
# BaseModel, ConfigDict, Field	Pydantic V2 核心组件：
# - BaseModel：数据模型基类，自带类型校验、序列化能力；
# - ConfigDict：模型全局配置；
# - Field：字段级配置，包含默认值、校验规则、描述等。
from __future__ import annotations

from datetime import datetime,timezone
from enum import StrEnum
from typing import Any,Literal

from pydantic import BaseModel,ConfigDict,Field

def utc_now()->datetime:
    return datetime.now(timezone.utc)


## 四、状态与分类枚举（全局标准值）
# 枚举是整个系统的 “统一语言”，所有模块对状态、分类的定义完全一致，避免出现字符串拼写错误、含义不一致。


# 1. 任务整体状态
# 描述**一整个任务（Run）**的生命周期状态：排队中 → 运行中 →（等待人工干预）→ 通过 / 失败 / 取消 / 预算超支。
class RunStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    WAITING_HUMAN = "waiting_human"
    PASSED = "passed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    BUDGET_EXCEEDED = "budget_exceeded"


# 2. 单步骤状态
# 描述**单个执行步骤（Step）**的状态，粒度比 RunStatus 更细：待执行 → 运行中 → 通过 / 失败 / 跳过。
class StepStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    PASSED = "passed"
    FAILED = "failed"
    SKIPPED = "skipped"


# 3. 失败分类
# 对错误原因做标准化分类，覆盖从拉取代码、规划、改代码、测试到安全、预算的全流程。

# - 作用：错误统计、根因分析、自动重试策略（比如环境问题可重试，安全问题不可重试）。
class FailureCategory(StrEnum):
    ENVIRONMENT = "environment"
    REPO_RETRIEVAL = "repo_retrieval"
    PLANNING = "planning"
    PATCH_APPLY = "patch_apply"
    TEST_FAILURE = "test_failure"
    REGRESSION = "regression"
    TOOL_ERROR = "tool_error"
    BUDGET = "budget"
    SECURITY = "security"
    UNKNOWN = "unknown"


# 4. 工具风险等级
# 对 Agent 调用的工具做安全分级：

# - `READ`：只读操作，无风险；
# - `WRITE`：修改文件 / 环境，有一定风险；
# - `DANGER`：高危操作（如执行命令、删除文件），需人工审核。
class ToolRisk(StrEnum):
    READ = "read"
    WRITE = "write"
    DANGER = "danger"


# 五、模型基类
# 所有数据契约的公共父类，统一了两条核心规则：

# 1. `extra="forbid"`：**禁止传入未定义的字段**。严格校验数据结构，防止脏数据、多余字段渗透进系统。
# 2. `populate_by_name=True`：初始化时既可以用字段名，也可以用字段别名，提升兼容性。

# 所有业务模型都继承它，保证全系统数据校验规则一致。
class BaseContract(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)



# 六、核心数据模型详解
# 1. ToolCall — 工具调用记录
# **对应 Agent 一次工具调用的完整审计记录**。

# - 标识：`id`（唯一 ID）、`tool`（工具名称）、`risk`（风险等级）；
# - 执行：`arguments`（调用参数）、`status`（执行状态）、时间字段、`duration_ms`（耗时，非负校验）；
# - 结果：`output_preview`（输出预览）、`error`（错误信息）；
# - 扩展：`metadata` 灵活存额外信息。
class ToolCall(BaseContract):
    id: str
    tool: str
    risk: ToolRisk = ToolRisk.READ
    arguments: dict[str, Any] = Field(default_factory=dict)
    status: StepStatus = StepStatus.PENDING
    started_at: datetime = Field(default_factory=utc_now)
    ended_at: datetime | None = None
    duration_ms: int | None = Field(default=None, ge=0)
    output_preview: str | None = None
    error: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)



# 2. Step — 执行步骤

# **任务执行的流程单元**，一个 `Run` 由多个 `Step` 按顺序组成。

# - `index`：步骤序号，从 0 开始，保证执行顺序；
# - `kind`：步骤类型，用 `Literal` 限定 8 种固定类型：规划、拉取代码、编辑、测试、诊断、验证、人工审核、收尾；
# - `tool_call_ids`：该步骤关联的所有工具调用 ID，通过 ID 关联 `ToolCall`，实现一对多关系。

class Step(BaseContract):
    index: int = Field(ge=0)
    kind: Literal[
        "plan", "retrieve", "edit", "test",
        "diagnose", "verify", "human_review", "finalize",
    ]
    status: StepStatus = StepStatus.PENDING
    summary: str = ""
    tool_call_ids: list[str] = Field(default_factory=list)
    started_at: datetime = Field(default_factory=utc_now)
    ended_at: datetime | None = None
    error: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


# 3. Metric — 指标项
# 通用度量数据结构，灵活记录各类指标：成本、耗时、通过率、代码行数等。

# - 支持多种数值类型，可扩展单位和元数据。
class Metric(BaseContract):
    name: str
    value: float | int | bool | str
    unit: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)



# 4. Failure — 失败详情
# 结构化的错误记录，替代零散的异常字符串。

# - 包含分类、错误信息、是否可重试、关联的步骤序号、证据（日志 / 截图），方便排查、统计和自动重试决策。
class Failure(BaseContract):
    category: FailureCategory = FailureCategory.UNKNOWN
    message: str
    retryable: bool = False
    step_index: int | None = Field(default=None, ge=0)
    evidence: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


# 5. Patch — 代码补丁  
# **一次代码变更的最小单元**。

# - 记录修改的文件路径、diff 内容、新增 / 删除行数，对应 Agent 对代码库的一次修改。
class Patch(BaseContract):
    path: str
    diff: str
    additions: int = Field(default=0, ge=0)
    deletions: int = Field(default=0, ge=0)
    id: str | None = None


# 6. Run — 主运行记录（最核心模型）
# **一次完整任务的全量快照**，聚合了所有执行数据，是系统中最核心的数据结构。

# - 版本控制：`schema_version` 固定为 `"1.0"`，未来结构大改时升级版本，保证兼容历史数据；
# - 基础信息：运行 ID、任务 ID、目标仓库、对应 issue、使用的 AI 模型；
# - 生命周期：状态 + 创建 / 开始 / 完成时间；
# - 执行数据：步骤列表、工具调用列表、指标列表、失败列表、补丁列表；
# - 成本与追踪：`cost_usd`（调用成本，非负）、`trace_id`（链路追踪 ID）。

# > 
# > 设计上是典型的**聚合根**：一个 Run 包含完整的执行链路，可完整复现一次任务的全过程，满足可审计、可追溯的核心需求。
class Run(BaseContract):
    schema_version: Literal["1.0"] = "1.0"
    id: str
    task_id: str
    repo: str
    issue: str
    model: str | None = None
    status: RunStatus = RunStatus.QUEUED
    created_at: datetime = Field(default_factory=utc_now)
    started_at: datetime | None = None
    completed_at: datetime | None = None
    steps: list[Step] = Field(default_factory=list)
    tool_calls: list[ToolCall] = Field(default_factory=list)
    metrics: list[Metric] = Field(default_factory=list)
    failures: list[Failure] = Field(default_factory=list)
    patches: list[Patch] = Field(default_factory=list)
    cost_usd: float | None = Field(default=None, ge=0)
    trace_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


# 7. RunSummary — 运行摘要
# **精简版 Run 模型**，用于列表页、概览页等场景。

# - 只保留核心字段，不包含完整的 steps/tool_calls 等大数组，大幅减少数据传输量，提升列表加载性能。
# - 典型的 DTO（数据传输对象）设计思想。
class RunSummary(BaseContract):
    id: str
    task_id: str
    repo: str
    issue: str
    status: RunStatus
    cost_usd: float | None = None
    duration_ms: int | None = Field(default=None, ge=0)
    patch_count: int = Field(default=0, ge=0)
    failure_count: int = Field(default=0, ge=0)
    created_at: datetime



# 七、`__all__` 导出声明
# 明确模块对外公开的所有接口，和包入口的 `__init__.py` 形成对应，规范 API 边界。
__all__ = [
    "BaseContract", "Failure", "FailureCategory", "Metric",
    "Patch", "Run", "RunStatus", "RunSummary",
    "Step", "StepStatus", "ToolCall", "ToolRisk", "utc_now",
]



## 八、整体设计特点总结

# 1. **契约驱动，模块解耦**：所有组件只依赖数据结构，不依赖彼此实现，模块间通过标准结构通信，可独立测试、部署、迭代。
# 2. **全链路可追溯**：`Run → Step → ToolCall` 三级嵌套结构，完整记录从任务到步骤再到每次工具调用的全流程，可审计、可回放、可排查。
# 3. **严格数据校验**：Pydantic 提供类型校验、字段范围校验、禁止额外字段，从源头杜绝脏数据。
# 4. **版本化与兼容性**：`schema_version` 字段保障结构演进时的向后兼容。
# 5. **原生可序列化**：所有模型可直接转 JSON / 字典，适配存储、消息队列、API 传输等所有场景。
# 6. **分层清晰**：枚举 → 基类 → 实体模型 → 摘要模型，职责分明，符合大型项目的