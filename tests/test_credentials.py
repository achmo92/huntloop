"""OPS-05: credentials are not readable from a copy of the database alone.

Covers three distinct behaviors:
1. Missing/malformed HUNTLOOP_SECRET_KEY fails fast at config-load time.
2. The MAIN app database file (and its WAL/SHM sidecars) never contains
   plaintext credential bytes, even after a credential has been written.
3. The CREDENTIALS store file contains only ciphertext, and reading it
   without HUNTLOOP_SECRET_KEY set raises rather than returning plaintext.
"""

from pathlib import Path

import pytest


def test_missing_secret_key_fails_fast(monkeypatch):
    from huntloop.config import ConfigError, load_config

    monkeypatch.delenv("HUNTLOOP_SECRET_KEY", raising=False)
    try:
        load_config()
        raise AssertionError("load_config() did not raise with HUNTLOOP_SECRET_KEY unset")
    except ConfigError as exc:
        assert "openssl rand -base64 32" in str(exc)

    monkeypatch.setenv("HUNTLOOP_SECRET_KEY", "not-a-valid-fernet-key")
    try:
        load_config()
        raise AssertionError("load_config() did not raise with a malformed HUNTLOOP_SECRET_KEY")
    except ConfigError as exc:
        assert "not a valid Fernet key" in str(exc)


def test_main_db_file_contains_no_plaintext_credential(main_engine, main_db_path, credentials_session):
    # Imports inside the body — see import discipline rule.
    from huntloop.credentials.store import CredentialStore
    from huntloop.db.repository import SettingsRepository

    secret = "sk-test-abc123-do-not-leak"

    store = CredentialStore(credentials_session)
    store.set("llm_api_key", secret)
    credentials_session.commit()

    # The main store keeps only settings *metadata* (key + is_secret flag) —
    # the plaintext/ciphertext value itself must never land in this table.
    settings_repo = SettingsRepository(main_engine)
    settings_repo.upsert_metadata(key="llm_api_key", is_secret=True)

    # Force a checkpoint so WAL contents land in the main file for this
    # assertion (01-RESEARCH.md Pitfall B).
    with main_engine.connect() as conn:
        conn.exec_driver_sql("PRAGMA wal_checkpoint(FULL)")

    targets = [
        main_db_path,
        Path(str(main_db_path) + "-wal"),
        Path(str(main_db_path) + "-shm"),
    ]
    for p in targets:
        if p.exists():
            assert secret.encode() not in p.read_bytes(), p


def test_credentials_file_is_ciphertext_only(credentials_session, credentials_db_path, monkeypatch):
    # Imports inside the body — see import discipline rule.
    from huntloop.credentials.store import CredentialStore

    secret = "sk-test-abc123-do-not-leak"

    store = CredentialStore(credentials_session)
    store.set("llm_api_key", secret)
    credentials_session.commit()

    with credentials_session.get_bind().connect() as conn:
        conn.exec_driver_sql("PRAGMA wal_checkpoint(FULL)")

    targets = [
        credentials_db_path,
        Path(str(credentials_db_path) + "-wal"),
        Path(str(credentials_db_path) + "-shm"),
    ]
    found_nonzero_file = False
    for p in targets:
        if p.exists():
            raw = p.read_bytes()
            assert secret.encode() not in raw, p
            if p == credentials_db_path:
                assert len(raw) > 0
                found_nonzero_file = True
    assert found_nonzero_file

    # Without the key, decrypting must raise rather than return plaintext.
    monkeypatch.delenv("HUNTLOOP_SECRET_KEY")
    with pytest.raises(Exception):  # noqa: B017 - broad on purpose, RED baseline
        CredentialStore(credentials_session).get("llm_api_key")
