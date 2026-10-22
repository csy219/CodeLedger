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