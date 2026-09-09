import os
import pytest
from huntloop.config import load_config, ConfigError

def test_openai_base_url_default(monkeypatch):
    monkeypatch.delenv("HUNTLOOP_OPENAI_BASE_URL", raising=False)
    config = load_config()
    assert config.openai_base_url == "https://api.openai.com/v1"

def test_openai_base_url_set(monkeypatch):
    monkeypatch.setenv("HUNTLOOP_OPENAI_BASE_URL", "http://localhost:8000/v1")
    config = load_config()
    assert config.openai_base_url == "http://localhost:8000/v1"

def test_openai_base_url_invalid_scheme(monkeypatch):
    monkeypatch.setenv("HUNTLOOP_OPENAI_BASE_URL", "ftp://x")
    with pytest.raises(ConfigError, match="http"):
        load_config()

def test_openai_base_url_empty(monkeypatch):
    monkeypatch.setenv("HUNTLOOP_OPENAI_BASE_URL", "   ")
    with pytest.raises(ConfigError, match="set but empty"):
        load_config()

def test_models_defaults(monkeypatch):
    monkeypatch.delenv("HUNTLOOP_TRIAGE_MODEL", raising=False)
    monkeypatch.delenv("HUNTLOOP_SCORING_MODEL", raising=False)
    monkeypatch.delenv("HUNTLOOP_EXTRACTION_MODEL", raising=False)
    config = load_config()
    assert config.triage_model == "gpt-4o-mini"
    assert config.scoring_model == "gpt-4o"
    assert config.extraction_model == "gpt-4o-mini"

def test_extraction_model_fallback(monkeypatch):
    monkeypatch.setenv("HUNTLOOP_TRIAGE_MODEL", "cheap-1")
    monkeypatch.delenv("HUNTLOOP_EXTRACTION_MODEL", raising=False)
    config = load_config()
    assert config.triage_model == "cheap-1"
    assert config.extraction_model == "cheap-1"

def test_concurrency_defaults_and_invalid(monkeypatch):
    monkeypatch.delenv("HUNTLOOP_MAX_EMPLOYER_CONCURRENCY", raising=False)
    config = load_config()
    assert config.max_employer_concurrency == 5
    
    monkeypatch.setenv("HUNTLOOP_MAX_EMPLOYER_CONCURRENCY", "notanint")
    with pytest.raises(ConfigError):
        load_config()

def test_stale_after_empty_runs_invalid(monkeypatch):
    monkeypatch.delenv("HUNTLOOP_STALE_AFTER_EMPTY_RUNS", raising=False)
    config = load_config()
    assert config.stale_after_empty_runs == 3
    
    monkeypatch.setenv("HUNTLOOP_STALE_AFTER_EMPTY_RUNS", "0")
    with pytest.raises(ConfigError, match=">= 1"):
        load_config()
