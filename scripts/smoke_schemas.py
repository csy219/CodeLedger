from __future__ import annotations

import json

from codeledger_core.schemas import Run, RunStatus


def main() -> None:
    run = Run(
        id="smoke-001",
        task_id="task-001",
        repo="acme/example",
        issue="#1",
        status=RunStatus.QUEUED,
    )

    payload = json.loads(run.model_dump_json())

    assert payload["schema_version"] == "1.0"
    assert payload["status"] == "queued"

    print("schema smoke: ok")
    print(json.dumps(payload, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()