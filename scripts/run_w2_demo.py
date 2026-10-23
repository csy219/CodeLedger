from __future__ import annotations

import os
import uuid
from pathlib import Path

from codeledger_core.agent import AgentConfig,AgentLoop
from codeledger_core.llm_policy import LLMConfig,LLMPolicy
from codeledger_sandbox.docker import DockerSandbox
from codeledger_core.tools import ToolRegistry
from codeledger_core.trace import TraceRecorder

def main()->None:
    api_key=os.environ.get("DEEPSEEK_API_KEY")
    if not api_key:
        raise SystemError("DEEPSEEK_API_KEY is required")

    workspace=Path("examples/buggy_calc").resolve()
    run_id=f"w2-demo-{uuid.uuid4().hex[:8]}"

    sandbox=DockerSandbox()
    registry=ToolRegistry(workspace,sandbox)
    trace=TraceRecorder(run_id=run_id,output_dir="runs")

    policy=LLMPolicy(
        LLMConfig(
            model="deepseek-chat",
            api_key=api_key,
        )
    )

    task = """
Fix the failing tests in the current repository.

The bug is in calculator.add.

You must:
1. Read the relevant file.
2. Apply a minimal patch.
3. Run tests with exactly:
   python -m unittest discover -s tests -q
4. When tests pass, call finish.

Do not modify test files.
""".strip()

    agent = AgentLoop(
        AgentConfig(
            max_steps=15,
            max_tool_calls=20,
            max_tokens=50_000,
            max_wall_time_seconds=300,
            max_repeated_actions=3,
            max_repair_attempts=2,
        )
    )

    result=agent.run(
        task=task,
        workspace=workspace,
        policy=policy,
        registry=registry,
        trace=trace,
    )

    print("success:", result.success)
    print("stop_reason:", result.stop_reason)
    print("steps:", result.steps)
    print("tool_calls:", result.tool_calls)
    print("tokens_used:", result.tokens_used)
    print("elapsed_ms:", result.elapsed_ms)
    print("final_output:", result.final_output)
    print("trace:", trace.path)


if __name__ == "__main__":
    main()
