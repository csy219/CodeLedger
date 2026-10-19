from __future__ import annotations
from pathlib import Path
from types import SimpleNamespace

import pytest

from codeledger_core.errors import ToolError, ToolFailureCategory
from codeledger_core.tools import ToolRegistry, read_file, run_tests

# 段 1：导入与 Fake 沙箱
# - 可配置的模拟沙箱，构造时指定退出码、输出、超时标记。
# - `run` 方法返回符合 `SandboxLike` 协议的模拟对象，不用启动真实 Docker。
# - 单元测试隔离外部依赖，只测 `run_tests` 的错误分类逻辑。
class FakeSandbox:
    def __init__(
        self,
        *,
        exit_code:int=0,
        stdout:str="",
        stderr:str="",
        timed_out:bool=False,
    )->None:
        self.exit_code=exit_code
        self.stdout=stdout
        self.stderr=stderr
        self.timed_out=timed_out

    def run(self, command: str, workspace: str | Path):
        return SimpleNamespace(
            exit_code=self.exit_code,
            stdout=self.stdout,
            stderr=self.stderr,
            timed_out=self.timed_out,
            container_name="fake",
        )

def test_timeout_failure(tmp_path) -> None:
    result = run_tests(
        FakeSandbox(exit_code=124, stderr="timeout", timed_out=True),
        tmp_path,
    )

    assert not result.ok
    assert result.failure is not None
    assert result.failure.category == ToolFailureCategory.TIMEOUT
    assert result.failure.retryable


def test_non_zero_exit_failure(tmp_path) -> None:
    result = run_tests(
        FakeSandbox(exit_code=1, stderr="assertion failed"),
        tmp_path,
    )

    assert not result.ok
    assert result.failure is not None
    assert result.failure.category == ToolFailureCategory.NON_ZERO_EXIT


def test_permission_denied_failure(tmp_path) -> None:
    result = run_tests(
        FakeSandbox(exit_code=1, stderr="Permission denied"),
        tmp_path,
    )

    assert result.failure is not None
    assert result.failure.category == ToolFailureCategory.PERMISSION_DENIED
    assert not result.failure.retryable


def test_output_truncation_warning(tmp_path) -> None:
    result = run_tests(
        FakeSandbox(stdout="x" * 25_000),
        tmp_path,
    )

    assert result.ok
    assert result.warnings
    assert result.warnings[0].category == ToolFailureCategory.OUTPUT_TRUNCATED
    assert "[truncated]" in result.output


def test_path_escape_failure(tmp_path) -> None:
    with pytest.raises(ToolError) as exc_info:
        read_file(tmp_path, "../secret.txt")

    assert exc_info.value.failure.category == ToolFailureCategory.PATH_ESCAPE


def test_registry_returns_structured_failure(tmp_path) -> None:
    registry = ToolRegistry(tmp_path, sandbox=FakeSandbox())
    result = registry.call("read_file", path="../secret.txt")

    assert not result.ok
    assert result.failure is not None
    assert result.failure.category == ToolFailureCategory.PATH_ESCAPE