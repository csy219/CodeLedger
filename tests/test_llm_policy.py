from __future__ import annotations

from codeledger_core.agent import ActionType
from codeledger_core.llm_policy import LLMConfig, LLMPolicy


class FakeResponse:
    def __init__(self, content: str, total_tokens: int = 123) -> None:
        self._content = content
        self._total_tokens = total_tokens

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return {
            "choices": [
                {
                    "message": {
                        "content": self._content,
                    }
                }
            ],
            "usage": {"total_tokens": self._total_tokens},
        }


class FakeClient:
    def __init__(self, response: FakeResponse) -> None:
        self.response = response
        self.last_request: dict | None = None

    def post(
        self,
        url: str,
        *,
        headers: dict,
        json: dict,
    ) -> FakeResponse:
        self.last_request = {
            "url": url,
            "headers": headers,
            "json": json,
        }
        return self.response


def test_llm_policy_parses_action_and_tokens() -> None:
    client = FakeClient(
        FakeResponse(
            '{"action":"read_file","arguments":{"path":"a.py"},"reason":"inspect"}',
            total_tokens=123,
        )
    )
    policy = LLMPolicy(
        LLMConfig(api_key="test-key"),
        client=client,
    )

    action = policy.decide(
        task="fix bug",
        observation="repo map",
        history=[],
    )

    assert action.action == ActionType.READ_FILE
    assert action.arguments["path"] == "a.py"
    assert action.tokens == 123


def test_llm_policy_removes_json_fence() -> None:
    client = FakeClient(
        FakeResponse(
            '```json\n{"action":"finish","arguments":{"answer":"done"}}\n```'
        )
    )
    policy = LLMPolicy(
        LLMConfig(api_key="test-key"),
        client=client,
    )

    action = policy.decide("task", "observation", [])

    assert action.action == ActionType.FINISH
    assert action.arguments["answer"] == "done"
