from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

# `ToolFailureCategory`：将所有工具错误归类为可枚举的固定类型，支持统计、筛选和自动化重试决策。
class ToolFailureCategory(StrEnum):
    TIMEOUT = "timeout"
    NON_ZERO_EXIT = "non_zero_exit"
    PERMISSION_DENIED = "permission_denied"
    OUTPUT_TRUNCATED = "output_truncated"
    INVALID_ARGUMENT = "invalid_argument"
    PATH_ESCAPE = "path_escape"
    UNKNOWN = "unknown"

# `ToolFailure`：结构化错误载体，比纯字符串多了分类、可重试标记、结构化详情字段，是错误可追溯、可处理的核心。
@dataclass(frozen=True)
class ToolFailure:
    category: ToolFailureCategory
    message: str
    retryable: bool = False
    details: dict[str, Any] = field(default_factory=dict)

# `ToolError`：运行时异常类，内部携带完整的 `ToolFailure` 对象，抛出异常时同步传递结构化错误信息。
class ToolError(RuntimeError):
    def __init__(
        self,
        message: str,
        category: ToolFailureCategory = ToolFailureCategory.UNKNOWN,
        retryable: bool = False,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.failure = ToolFailure(
            category=category,
            message=message,
            retryable=retryable,
            details=details or {},
        )


__all__ = ["ToolError", "ToolFailure", "ToolFailureCategory"]

