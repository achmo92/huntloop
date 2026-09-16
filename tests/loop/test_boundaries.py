"""LOOP-08 structural import-scan guard over ``src/huntloop/loop/``.

Owned by plan 05-01. Mirrors ``tests/scoring/test_client_routing.py``'s OPS-06
boundary test: a plain text scan over every module in the package, so the
boundary is enforced by the test suite rather than by policy alone.

Two invariants:

1. No module in ``huntloop.loop`` may reach the scoring rubric or its scale
   anchors. LOOP-08 says the system never proposes changes to the rubric, and
   the strongest guarantee is that the rubric's configuration module is not in
   the package's import graph at all.
2. Only ``rationale.py`` may reach the model client. Detection, thresholding
   and predicted-effect computation are deterministic by construction.
"""

from pathlib import Path

LOOP_DIR = Path(__file__).resolve().parents[2] / "src" / "huntloop" / "loop"
RUBRIC_FORBIDDEN = ("huntloop.scoring.config", "SCALE_ANCHORS")
LLM_FORBIDDEN = ("import openai", "from openai", "huntloop.llm")
LLM_ALLOWED_FILE = "rationale.py"


def test_loop_package_respects_structural_boundaries():
    # Guard against a vacuous pass: if the package goes missing, the scan
    # below would iterate zero files and assert nothing.
    assert LOOP_DIR.exists(), f"{LOOP_DIR} is missing — the boundary guard cannot run"

    scanned = sorted(LOOP_DIR.rglob("*.py"))
    assert scanned, f"no Python modules found under {LOOP_DIR}"

    for path in scanned:
        text = path.read_text(encoding="utf-8")

        for token in RUBRIC_FORBIDDEN:
            assert token not in text, (
                f"LOOP-08 violation: {path.name} mentions {token!r}. Nothing under "
                f"huntloop.loop may reach the scoring rubric."
            )

        if path.name == LLM_ALLOWED_FILE:
            continue

        for token in LLM_FORBIDDEN:
            assert token not in text, (
                f"LOOP-08 violation: {path.name} mentions {token!r}. Only "
                f"{LLM_ALLOWED_FILE} may reach the model client."
            )
