from __future__ import annotations

import json

from codeledger_core.tools import ToolResult
from codeledger_core.trace import (
    TraceEventType,
    TraceRecorder,
    TracedToolRegistry,
)


def test_trace_recorder_writes_jsonl(tmp_path) -> None:
    trace = TraceRecorder(run_id="run-1", output_dir=tmp_path)

    trace.record(TraceEventType.RUN_STARTED, data={"task": "demo"})
    trace.record(TraceEventType.STEP_STARTED, step_index=0)

    events = TraceRecorder.read_events(trace.path)

    assert len(events) == 2
    assert events[0].sequence == 1
    assert events[1].sequence == 2
    assert events[0].event_type == TraceEventType.RUN_STARTED
    assert events[1].step_index == 0


def test_trace_summary(tmp_path) -> None:
    trace = TraceRecorder(run_id="run-2", output_dir=tmp_path)

    trace.record(TraceEventType.RUN_STARTED)
    trace.record(
        TraceEventType.TOOL_CALL_FINISHED,
        tool_call_id="tool-1",
        duration_ms=10,
        data={"tool": "read_file"},
    )
    trace.record(TraceEventType.ERROR, error="boom")

    summary = trace.summary()

    assert summary["event_count"] == 3
    assert summary["tool_calls"] == 1
    assert summary["errors"] == 1


def test_traced_tool_registry_records_events(tmp_path) -> None:
    class FakeRegistry:
        def call(self, name: str, **kwargs):
            return ToolResult(ok=True, output="ok")

    trace = TraceRecorder(run_id="run-3", output_dir=tmp_path)
    registry = TracedToolRegistry(FakeRegistry(), trace)

    result = registry.call("read_file", path="a.py")

    events = TraceRecorder.read_events(trace.path)

    assert result.ok
    assert [e.event_type for e in events] == [
        TraceEventType.TOOL_CALL_STARTED,
        TraceEventType.TOOL_CALL_FINISHED,
    ]
    assert events[0].tool_call_id == events[1].tool_call_id
    assert events[1].duration_ms is not None