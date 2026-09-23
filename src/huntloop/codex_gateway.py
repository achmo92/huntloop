"""Local OpenAI-compatible gateway backed by the authenticated Codex CLI.

Run this module on the host, not inside HuntLoop's containers. Codex keeps
ownership of its OAuth credentials; HuntLoop sees only this local HTTP API.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import subprocess
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Any

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

app = FastAPI(title="HuntLoop Codex Gateway", docs_url=None, redoc_url=None)

_MODEL_PATTERN = re.compile(r"^[A-Za-z0-9._:-]+$")
_CODEX_LOCK = threading.Lock()


class Message(BaseModel):
    role: str
    content: str = Field(max_length=1_000_000)


class ChatCompletionRequest(BaseModel):
    model: str
    messages: list[Message]
    temperature: float | None = None
    response_format: dict[str, Any] | None = None
    max_tokens: int | None = None


@dataclass(frozen=True)
class CodexResult:
    content: str
    prompt_tokens: int
    completion_tokens: int


def _authorize(authorization: str | None) -> None:
    gateway_key = os.environ.get("CODEX_GATEWAY_KEY")
    if not gateway_key:
        raise HTTPException(status_code=503, detail="CODEX_GATEWAY_KEY is not configured")
    expected = f"Bearer {gateway_key}"
    if authorization is None or not secrets.compare_digest(authorization, expected):
        raise HTTPException(status_code=401, detail="Invalid gateway key")


def _prompt(messages: list[Message]) -> str:
    transcript = "\n\n".join(f"{message.role.upper()}:\n{message.content}" for message in messages)
    return (
        f"{transcript}\n\n"
        "Return only the requested JSON object. Do not include Markdown fences or commentary."
    )


def _parse_codex_events(stdout: str) -> CodexResult:
    content: str | None = None
    prompt_tokens = 0
    completion_tokens = 0

    for raw_line in stdout.splitlines():
        if not raw_line.strip():
            continue
        try:
            event = json.loads(raw_line)
        except json.JSONDecodeError as exc:
            raise ValueError("Codex emitted invalid JSON event output") from exc

        if event.get("type") == "item.completed":
            item = event.get("item") or {}
            if item.get("type") == "agent_message" and isinstance(item.get("text"), str):
                content = item["text"]
        elif event.get("type") == "turn.completed":
            usage = event.get("usage") or {}
            prompt_tokens = int(usage.get("input_tokens") or 0)
            completion_tokens = int(usage.get("output_tokens") or 0)

    if content is None:
        raise ValueError("Codex did not emit a completed agent message")

    return CodexResult(content, prompt_tokens, completion_tokens)


def run_codex(prompt: str, *, model: str) -> CodexResult:
    if not _MODEL_PATTERN.fullmatch(model):
        raise ValueError("Model contains unsupported characters")

    command = os.environ.get("CODEX_COMMAND", "codex")
    try:
        timeout = int(os.environ.get("CODEX_GATEWAY_TIMEOUT_SECONDS", "120"))
    except ValueError as exc:
        raise RuntimeError("CODEX_GATEWAY_TIMEOUT_SECONDS must be an integer") from exc

    args = [
        command,
        "exec",
        "--json",
        "--ephemeral",
        "--ignore-rules",
        "--ignore-user-config",
        "--sandbox",
        "read-only",
        "--skip-git-repo-check",
    ]
    if model != "codex":
        args.extend(["--model", model])
    args.append("-")

    try:
        # ponytail: serialize local CLI calls; add a worker queue only if throughput requires it.
        with _CODEX_LOCK, tempfile.TemporaryDirectory() as workdir:
            completed = subprocess.run(
                args,
                input=prompt,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
                cwd=workdir,
            )
    except FileNotFoundError as exc:
        raise RuntimeError(f"Codex CLI was not found at {command!r}") from exc
    except subprocess.TimeoutExpired as exc:
        raise TimeoutError(f"Codex exceeded the {timeout}-second timeout") from exc

    if completed.returncode != 0:
        raise RuntimeError(f"Codex exited with status {completed.returncode}")

    return _parse_codex_events(completed.stdout)


@app.get("/v1/models")
def models(authorization: str | None = Header(default=None)) -> dict[str, Any]:
    _authorize(authorization)
    configured = os.environ.get("CODEX_GATEWAY_MODELS", "codex")
    model_ids = [value.strip() for value in configured.split(",") if value.strip()]
    return {
        "object": "list",
        "data": [{"id": model_id, "object": "model"} for model_id in model_ids],
    }


@app.post("/v1/chat/completions")
def chat_completions(
    body: ChatCompletionRequest,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    _authorize(authorization)

    try:
        result = run_codex(_prompt(body.messages), model=body.model)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except TimeoutError as exc:
        raise HTTPException(status_code=504, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return {
        "id": f"codex-local-{uuid.uuid4()}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": body.model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": result.content},
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": result.prompt_tokens,
            "completion_tokens": result.completion_tokens,
            "total_tokens": result.prompt_tokens + result.completion_tokens,
        },
    }
