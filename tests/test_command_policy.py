from __future__ import annotations

import pytest

from codeledger_sandbox.policy import CommandPolicy, CommandRejected


def test_allows_pytest() -> None:
    policy = CommandPolicy()
    assert policy.check("pytest -q").allowed


def test_allows_python_module_pytest() -> None:
    policy = CommandPolicy()
    assert policy.check("python -m pytest -q").allowed


def test_allows_read_only_git() -> None:
    policy = CommandPolicy()
    assert policy.check("git diff --stat").allowed


def test_blocks_rm_rf() -> None:
    policy = CommandPolicy()
    decision = policy.check("rm -rf /")
    assert not decision.allowed


def test_blocks_git_push() -> None:
    policy = CommandPolicy()
    decision = policy.check("git push")
    assert not decision.allowed


def test_blocks_shell_operator() -> None:
    policy = CommandPolicy()
    decision = policy.check("pytest -q; rm -rf /")
    assert not decision.allowed


def test_assert_allowed_raises() -> None:
    policy = CommandPolicy()
    with pytest.raises(CommandRejected):
        policy.assert_allowed("curl https://example.com")