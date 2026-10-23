# 这段代码是 `codeledger_core` 包的顶层入口（通常就是包根目录下的 `__init__.py`），它做了两件核心事：

# 1. 从内部的 `schemas` 子模块导入所有核心数据结构与工具函数；
# 2. 通过 `__all__` 明确声明**整个包对外公开的接口列表**。

# 通俗说：它就像一个 “前台接待”，把包里定义好的核心数据模型统一摆出来，外部使用者只需要从顶层包导入即可，不用关心内部的文件目录结构。

"""CodeLedger core contracts and harness primitives."""

# - 新增 errors 模块的顶层导入。
# - 新增 tools 模块所有公开类型的顶层导入。
# - 外部使用者只需要从 `codeledger_core` 顶层包就能导入所有需要的类型，不用深入内部子模块。
from codeledger_core.errors import ToolError, ToolFailure, ToolFailureCategory
from codeledger_core.tools import (
    SandboxLike,
    SearchMatch,
    ToolRegistry,
    ToolResult,
    apply_patch,
    git_diff,
    read_file,
    run_tests,
    search_code,
)

# 作用：把追踪模块的核心类型提升到包顶层，外部使用者不用深入子模块，直接从 `codeledger_core` 顶层导入即可。
from codeledger_core.trace import (
    TraceEvent,
    TraceEventType,
    TraceRecorder,
    TracedToolRegistry,
)

#构建仓库地图(仓库全部内容进来会打爆上下文)
from codeledger_core.repo_map import (
    RepoFile,
    RepoMap,
    RepoMapBuilder,
)


# 单Agent-loop
from codeledger_core.agent import (
    ActionType,
    AgentAction,
    AgentConfig,
    AgentLoop,
    AgentRunResult,
    Policy,
    ScriptedPolicy,
)



# 大模型的真实决策
from codeledger_core.llm_policy import LLMConfig, LLMPolicy




# 这部分从 `codeledger_core.schemas`（内部专门放数据模型的模块）导入 13 个对象，按功能可分为三类：
# 类型	包含对象	作用
# 状态 / 分类枚举	RunStatus、StepStatus、FailureCategory、ToolRisk	定义运行、步骤、失败、风险的标准状态 / 分类值，保证全系统状态统一
# 核心实体数据类	Run、Step、ToolCall、Failure、Metric、Patch、RunSummary	系统的核心数据模型：运行记录、执行步骤、工具调用、失败详情、指标、补丁变更、运行汇总
# 工具函数	utc_now	获取当前 UTC 时间的工具函数，用于统一时间戳格式
from codeledger_core.schemas import (
    Failure,
    FailureCategory,
    Metric,
    Patch,
    Run,
    RunStatus,
    RunSummary,
    Step,
    StepStatus,
    ToolCall,
    ToolRisk,
    utc_now,
)


# `__all__` 是 Python 的**特殊变量**，作用是：
# - 定义模块的**公开导出名单**：当外部使用 `from codeledger_core import *` 通配符导入时，**只会导入 `__all__` 里列出的名字**；
# - 明确 API 边界：告诉使用者哪些是稳定的、可以放心使用的公开接口，没在列表里的属于内部实现，不保证兼容；
# - 提升可维护性：一眼就能看清这个包对外提供了哪些能力，相当于自带 “API 目录”。
__all__ = [
    "Failure",
    "FailureCategory",
    "Metric",
    "Patch",
    "Run",
    "RunStatus",
    "RunSummary",
    "Step",
    "StepStatus",
    "ToolCall",
    "ToolRisk",
    "utc_now",
    "SandboxLike",
    "SearchMatch",
    "ToolError",
    "ToolFailure",
    "ToolFailureCategory",
    "ToolRegistry",
    "ToolResult",
    "apply_patch",
    "git_diff",
    "read_file",
    "run_tests",
    "search_code",
    "TraceEvent",
    "TraceEventType",
    "TraceRecorder",
    "TracedToolRegistry",
    "RepoFile",
    "RepoMap",
    "RepoMapBuilder",
    "ActionType",
    "AgentAction",
    "AgentConfig",
    "AgentLoop",
    "AgentRunResult",
    "Policy",
    "ScriptedPolicy",
    "LLMConfig",
    "LLMPolicy",
]

## 三、这么写的设计好处

# 1. **简化导入路径**
# 外部使用者只需要写：

# ```
# from codeledger_core import Run, Step, Failure
# ```

# 而不需要深入内部层级：

# ```
# from codeledger_core.schemas import Run, Step, Failure
# ```
# 2. **封装内部结构**
# 所有数据模型的具体实现都放在 `schemas.py` 里，顶层只做聚合导出。未来即使内部文件拆分、重构，只要顶层导出不变，外部代码就不受影响。
# 3. **严格的 API 治理**
# 通过 `__all__` 明确 “哪些是对外承诺的稳定接口”，避免内部工具类、辅助函数被外部意外依赖，保障版本兼容性。
# 4. **符合 Python 大型项目最佳实践**
# 对于核心领域模型集中的项目，通常都会把数据定义收敛到 `schemas` / `models` 子模块，再通过 `__init__.py` 统一导出，是工业界的标准写法。