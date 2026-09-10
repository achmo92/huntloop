import subprocess
import sys
from huntloop.scoring.config import (
    active_scoring_config, compute_scoring_config_version, DIMENSIONS,
    SCALE_ANCHORS, SAMPLING_PARAMS, persist_scoring_config, load_scoring_config
)

def test_dimensions_and_anchors():
    assert len(DIMENSIONS) == 4
    assert [d.name for d in DIMENSIONS] == ["role_fit", "seniority_fit", "employer_fit", "trajectory"]
    assert set(SCALE_ANCHORS.keys()) == {1, 2, 3, 4, 5}
    assert "meets the core requirement" in SCALE_ANCHORS[3]
    assert SAMPLING_PARAMS == {"temperature": 0.0, "top_p": 1.0, "n": 1}

def test_active_scoring_config_version():
    cfg = active_scoring_config()
    v = cfg.version
    assert len(v) == 16
    assert all(c in '0123456789abcdef' for c in v)
    assert v == active_scoring_config().version

def test_hash_is_deterministic():
    in_process = active_scoring_config().version
    res = subprocess.run([sys.executable, "-c", "from huntloop.scoring.config import active_scoring_config as a; print(a().version)"], capture_output=True, text=True)
    out = res.stdout.strip()
    assert out == in_process

def test_hash_changes_on_guidance_change():
    base = active_scoring_config().version
    new_dims = list(DIMENSIONS)
    import dataclasses
    new_dims[0] = dataclasses.replace(new_dims[0], guidance="new guidance")
    new_hash = compute_scoring_config_version(
        triage_prompt="a", scoring_prompt="b", dimensions=tuple(new_dims),
        scale_anchors=SCALE_ANCHORS, sampling_params=SAMPLING_PARAMS
    )
    assert new_hash != base

def test_hash_changes_on_anchor_change():
    base = active_scoring_config().version
    new_anchors = dict(SCALE_ANCHORS)
    new_anchors[1] = "different"
    new_hash = compute_scoring_config_version(
        triage_prompt="a", scoring_prompt="b", dimensions=DIMENSIONS,
        scale_anchors=new_anchors, sampling_params=SAMPLING_PARAMS
    )
    assert new_hash != base

def test_hash_changes_on_sampling_change():
    base = active_scoring_config().version
    new_hash = compute_scoring_config_version(
        triage_prompt="a", scoring_prompt="b", dimensions=DIMENSIONS,
        scale_anchors=SCALE_ANCHORS, sampling_params={"temperature": 1.0}
    )
    assert new_hash != base

def test_hash_independent_of_sampling_keys_order():
    h1 = compute_scoring_config_version(
        "a", "b", DIMENSIONS, SCALE_ANCHORS, {"temperature": 0.0, "top_p": 1.0}
    )
    h2 = compute_scoring_config_version(
        "a", "b", DIMENSIONS, SCALE_ANCHORS, {"top_p": 1.0, "temperature": 0.0}
    )
    assert h1 == h2

def test_persist_and_load(main_session):
    cfg = active_scoring_config()
    v = persist_scoring_config(main_session, cfg)
    assert v == cfg.version
    
    # idempotent
    persist_scoring_config(main_session, cfg)
    
    loaded = load_scoring_config(main_session, v)
    assert loaded is not None
    assert loaded["triage_prompt"] == cfg.triage_prompt
    assert loaded["version"] == cfg.version

def test_load_missing(main_session):
    assert load_scoring_config(main_session, "deadbeefdeadbeef") is None
