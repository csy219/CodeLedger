from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from codeledger_core.tools import (
    ToolError,
    ToolRegistry,
    apply_patch,
    git_diff,
    read_file,
    run_tests,
    search_code,
)


class FakeSandbox:
    def run(self, command: str, workspace: str | Path):
        return SimpleNamespace(
            exit_code=0,
            stdout="tests passed",
            stderr="",
            timed_out=False,
            container_name="fake",
        )


def test_search_code(tmp_path) -> None:
    (tmp_path / "sample.py").write_text(
        "def add(a, b):\n    return a + b\n",
        encoding="utf-8",
    )

    result = search_code(tmp_path, "return a + b")

    assert result.ok
    assert result.metadata["matches"][0]["path"] == "sample.py"
    assert result.metadata["matches"][0]["line"] == 2


def test_read_file(tmp_path) -> None:
    (tmp_path / "sample.py").write_text("one\ntwo\nthree\n", encoding="utf-8")

    result = read_file(tmp_path, "sample.py", start_line=2, end_line=3)

    assert result.ok
    assert "2: two" in result.output
    assert "3: three" in result.output


def test_apply_patch(tmp_path) -> None:
    target = tmp_path / "sample.py"
    target.write_text("value = 1\n", encoding="utf-8")

    result = apply_patch(
        tmp_path,
        "sample.py",
        old_text="value = 1",
        new_text="value = 2",
    )

    assert result.ok
    assert target.read_text(encoding="utf-8") == "value = 2\n"
    assert "+value = 2" in result.output


def test_git_diff(tmp_path) -> None:
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)

    result = git_diff(tmp_path)

    assert result.ok


def test_run_tests_with_fake_sandbox(tmp_path) -> None:
    result = run_tests(FakeSandbox(), tmp_path, "pytest -q")

    assert result.ok
    assert result.output == "tests passed"


def test_path_escape_is_rejected(tmp_path) -> None:
    with pytest.raises(ToolError):
        read_file(tmp_path, "../secret.txt")


def test_tool_registry_unknown_tool(tmp_path) -> None:
    registry = ToolRegistry(tmp_path, sandbox=FakeSandbox())
    result = registry.call("not_a_tool")

    assert not result.ok
    assert "unknown tool" in (result.error or "")