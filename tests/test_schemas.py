from __future__ import annotations

from codeledger_core.schemas import (
    Failure,
    FailureCategory,
    Metric,
    Patch,
    Run,
    RunStatus,
    Step,
    StepStatus,
    ToolCall,
    ToolRisk,
)


def test_run_contract_round_trip() -> None:
    run = Run(
        id="run-001",
        task_id="swe-001",
        repo="acme/pipeline",
        issue="#123",
        model="test-model",
        status=RunStatus.RUNNING,
        steps=[
            Step(
                index=0,
                kind="plan",
                status=StepStatus.PASSED,
                summary="locate parser",
            ),
            Step(
                index=1,
                kind="edit",
                status=StepStatus.RUNNING,
                summary="apply patch",
            ),
        ],
        tool_calls=[
            ToolCall(
                id="call-1",
                tool="search_code",
                risk=ToolRisk.READ,
                arguments={"query": "parse_config"},
                status=StepStatus.PASSED,
                duration_ms=12,
            )
        ],
        metrics=[
            Metric(name="token_usage", value=1200, unit="tokens")
        ],
        failures=[
            Failure(
                category=FailureCategory.TEST_FAILURE,
                message="test_parser failed",
                retryable=True,
                step_index=1,
            )
        ],
        patches=[
            Patch(path="config/parser.py", diff="+ guard", additions=1)
        ],
        cost_usd=0.12,
    )

    raw = run.model_dump_json()
    loaded = Run.model_validate_json(raw)

    assert loaded.id == run.id
    assert loaded.status == RunStatus.RUNNING
    assert loaded.tool_calls[0].tool == "search_code"
    assert loaded.failures[0].category == FailureCategory.TEST_FAILURE