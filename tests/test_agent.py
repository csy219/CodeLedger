from __future__ import annotations

from codeledger_core.agent import (
    ActionType,
    AgentAction,
    AgentConfig,
    AgentLoop,
    ScriptedPolicy,
)
from codeledger_core.tools import ToolResult
from codeledger_core.trace import TraceEventType, TraceRecorder


from codeledger_core.agent import StopReason

class FakeRegistry:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def call(self, name: str, **kwargs) -> ToolResult:
        self.calls.append((name, kwargs))
        return ToolResult(ok=True, output=f"result of {name}")


def test_agent_loop_finishes(tmp_path) -> None:
    policy = ScriptedPolicy(
        [
            AgentAction(
                action=ActionType.SEARCH_CODE,
                arguments={"query": "hello"},
            ),
            AgentAction(
                action=ActionType.FINISH,
                arguments={"answer": "done"},
            ),
        ]
    )

    result = AgentLoop(AgentConfig(max_steps=5)).run(
        task="find hello",
        workspace=tmp_path,
        policy=policy,
        registry=FakeRegistry(),
    )

    assert result.success
    assert result.final_output == "done"
    assert result.steps == 2
    assert result.tool_calls == 1


def test_agent_loop_max_steps(tmp_path) -> None:
    policy = ScriptedPolicy(
        [
            AgentAction(
                action=ActionType.SEARCH_CODE,
                arguments={"query": "hello"},
            )
        ]
        * 5
    )

    result = AgentLoop(AgentConfig(max_steps=2)).run(
        task="loop forever",
        workspace=tmp_path,
        policy=policy,
        registry=FakeRegistry(),
    )

    assert not result.success
    assert result.steps == 2
    assert result.error == "max steps reached"


def test_agent_loop_records_trace(tmp_path) -> None:
    trace = TraceRecorder(run_id="run-agent", output_dir=tmp_path)
    policy = ScriptedPolicy(
        [
            AgentAction(
                action=ActionType.READ_FILE,
                arguments={"path": "a.py"},
            ),
            AgentAction(
                action=ActionType.FINISH,
                arguments={"answer": "done"},
            ),
        ]
    )

    result = AgentLoop(AgentConfig(max_steps=5)).run(
        task="read a file",
        workspace=tmp_path,
        policy=policy,
        registry=FakeRegistry(),
        trace=trace,
    )

    events = TraceRecorder.read_events(trace.path)
    event_types = [event.event_type for event in events]

    assert result.success
    assert TraceEventType.RUN_STARTED in event_types
    assert TraceEventType.STEP_STARTED in event_types
    assert TraceEventType.TOOL_CALL_STARTED in event_types
    assert TraceEventType.TOOL_CALL_FINISHED in event_types
    assert TraceEventType.RUN_FINISHED in event_types




def test_agent_token_budget(tmp_path) -> None:
    policy = ScriptedPolicy(
        [
            AgentAction(
                action=ActionType.SEARCH_CODE,
                arguments={"query": "hello"},
                tokens=60,
            )
        ]
    )

    result = AgentLoop(
        AgentConfig(max_steps=5, max_tokens=50)
    ).run(
        task="token budget",
        workspace=tmp_path,
        policy=policy,
        registry=FakeRegistry(),
    )

    assert not result.success
    assert result.stop_reason == StopReason.MAX_TOKENS
    assert result.tokens_used == 60


def test_agent_repeated_action(tmp_path) -> None:
    action = AgentAction(
        action=ActionType.SEARCH_CODE,
        arguments={"query": "same"},
    )
    policy = ScriptedPolicy([action, action, action])

    result = AgentLoop(
        AgentConfig(max_steps=5, max_repeated_actions=1)
    ).run(
        task="repeat",
        workspace=tmp_path,
        policy=policy,
        registry=FakeRegistry(),
    )

    assert not result.success
    assert result.stop_reason == StopReason.REPEATED_ACTION


def test_agent_time_budget(tmp_path) -> None:
    policy = ScriptedPolicy([])

    result = AgentLoop(
        AgentConfig(max_steps=5, max_wall_time_seconds=0)
    ).run(
        task="timeout",
        workspace=tmp_path,
        policy=policy,
        registry=FakeRegistry(),
    )

    assert not result.success
    assert result.stop_reason == StopReason.TIME_LIMIT