from __future__ import annotations

import json
from pathlib import Path

# TRAJECTORY：刚刚 mini-SWE-agent 生成的轨迹文件。
TRAJECTORY = Path("eval/swebench/runs/baseline_sympy_15599.json")
OUTPUT = Path("eval/swebench/runs/preds.json")
INSTANCE_ID = "sympy__sympy-15599"
MODEL_NAME = "deepseek/deepseek-chat"


def main() -> None:
    trajectory = json.loads(TRAJECTORY.read_text(encoding="utf-8"))

    # info.submission：Agent 最终提交的 patch。
    submission = trajectory["info"]["submission"]

    # predictions：SWE-bench 要求的预测格式。
    # model_patch：真正要评测的代码改动。
    # model_name_or_path：记录是哪个模型生成的。
    predictions = {
        INSTANCE_ID: {
            "instance_id": INSTANCE_ID,
            "model_name_or_path": MODEL_NAME,
            "model_patch": submission,
        }
    }

    OUTPUT.write_text(
        json.dumps(predictions, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"wrote {OUTPUT}")
    print(f"patch chars: {len(submission)}")


if __name__ == "__main__":
    main()