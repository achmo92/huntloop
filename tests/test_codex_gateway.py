import json

from fastapi.testclient import TestClient

from huntloop import codex_gateway


def test_parse_codex_events_extracts_message_and_usage():
    stdout = "\n".join(
        [
            json.dumps({"type": "thread.started", "thread_id": "thread-1"}),
            json.dumps(
                {
                    "type": "item.completed",
                    "item": {"type": "agent_message", "text": '{"keep":true}'},
                }
            ),
            json.dumps(
                {
                    "type": "turn.completed",
                    "usage": {"input_tokens": 12, "output_tokens": 4},
                }
            ),
        ]
    )

    result = codex_gateway._parse_codex_events(stdout)

    assert result.content == '{"keep":true}'
    assert result.prompt_tokens == 12
    assert result.completion_tokens == 4


def test_chat_completion_uses_openai_compatible_shape(monkeypatch):
    monkeypatch.setenv("CODEX_GATEWAY_KEY", "test-key")
    monkeypatch.setattr(
        codex_gateway,
        "run_codex",
        lambda prompt, *, model: codex_gateway.CodexResult('{"keep":true}', 12, 4),
    )
    client = TestClient(codex_gateway.app)

    response = client.post(
        "/v1/chat/completions",
        headers={"Authorization": "Bearer test-key"},
        json={
            "model": "gpt-5.3-codex",
            "messages": [
                {"role": "system", "content": "Return a decision."},
                {"role": "user", "content": "Evaluate this listing."},
            ],
            "response_format": {"type": "json_object"},
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["choices"][0]["message"]["content"] == '{"keep":true}'
    assert body["usage"] == {
        "prompt_tokens": 12,
        "completion_tokens": 4,
        "total_tokens": 16,
    }


def test_gateway_rejects_invalid_key(monkeypatch):
    monkeypatch.setenv("CODEX_GATEWAY_KEY", "expected")
    client = TestClient(codex_gateway.app)

    response = client.get(
        "/v1/models",
        headers={"Authorization": "Bearer wrong"},
    )

    assert response.status_code == 401
