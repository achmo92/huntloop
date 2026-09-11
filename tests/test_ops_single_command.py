"""OPS-01: the whole system starts with a single command (Docker Compose).

These are config-level proofs — they run `docker compose config` and inspect the
compose/Dockerfile text, so they keep the single-command contract honest on every
edit without needing a running Docker daemon. The LIVE proof
(`docker compose up -d --build` -> /api/health + SPA + /api/dashboard on :8000) is
executed by plan 04-11 Task 2 and recorded in its SUMMARY.

UI-01's any-device reachability is asserted here at the port-binding level
(`8000:8000`, not `127.0.0.1:8000:8000`); the real second-device test is the
phase-4 human sign-off checkpoint.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
COMPOSE_PATH = REPO_ROOT / "docker-compose.yml"
DOCKERFILE_PATH = REPO_ROOT / "Dockerfile"


def _compose_env() -> dict[str, str]:
    env = dict(os.environ)
    # HUNTLOOP_SECRET_KEY is `${VAR:?...}` in the compose file. Parsing the
    # compose contract must not depend on a developer's local `.env`, so supply
    # a throwaway value when the caller has not set one.
    env.setdefault("HUNTLOOP_SECRET_KEY", "ops-single-command-test-key")
    return env


def _run_compose(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["docker", "compose", *args],
        cwd=REPO_ROOT,
        env=_compose_env(),
        capture_output=True,
        text=True,
        check=False,
    )


def _compose() -> dict:
    return yaml.safe_load(COMPOSE_PATH.read_text())


def test_single_command_starts_full_stack():
    """`docker compose up -d` brings up the long/one-shot essentials together."""
    result = _run_compose("config", "--services")
    assert result.returncode == 0, result.stderr
    services = set(result.stdout.split())
    # migrate (migrations), scheduler (unattended runs), web (browser surface).
    # `app` is the one-shot CLI runner and `probe` is behind the dev profile;
    # neither is required for the browser promise.
    assert {"migrate", "scheduler", "web"} <= services


def test_web_service_single_worker():
    """STACK.md: multi-worker + in-process scheduler duplicates runs silently."""
    command = _compose()["services"]["web"]["command"]
    if isinstance(command, str):
        command = command.split()
    assert "--workers" in command
    assert command[command.index("--workers") + 1] == "1"


def test_web_reaches_all_devices():
    """UI-01: bind the host interface (0.0.0.0), not loopback-only."""
    ports = [str(port) for port in _compose()["services"]["web"]["ports"]]
    assert "8000:8000" in ports
    assert not any("127.0.0.1" in port for port in ports)


def test_dockerfile_builds_frontend():
    """The image bakes the SPA and keeps the Playwright chromium layer."""
    text = DOCKERFILE_PATH.read_text()
    assert "FROM node" in text
    assert "COPY --from=webbuild" in text
    assert "playwright install --with-deps chromium" in text
