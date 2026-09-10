"""OPS-05: credentials are not readable from a copy of the database alone.

Covers three distinct behaviors:
1. Missing/malformed HUNTLOOP_SECRET_KEY fails fast at config-load time.
2. The MAIN app database file (and its WAL/SHM sidecars) never contains
   plaintext OR ciphertext credential bytes, even after a credential has
   been written and its metadata recorded in `settings`.
3. The CREDENTIALS store file contains only ciphertext, and reading it
   without HUNTLOOP_SECRET_KEY set raises rather than returning plaintext.
"""

from pathlib import Path

import pytest
from cryptography.fernet import Fernet

from huntloop.config import ConfigError
from huntloop.credentials.base import CredentialsBase
from huntloop.credentials.models import Credential
from huntloop.credentials.store import CredentialDecryptError, CredentialStore
from huntloop.db.repository import SettingsRepository


def test_missing_secret_key_fails_fast(monkeypatch, tmp_path):
    from huntloop.config import load_config

    # A developer's .env must not re-supply HUNTLOOP_SECRET_KEY here:
    # find_dotenv walks up from the calling module's directory, so the only
    # reliable isolation is to no-op the dotenv load.
    monkeypatch.setattr("huntloop.config.dotenv.load_dotenv", lambda *a, **k: False)
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


def test_main_db_file_contains_no_plaintext_credential(
    main_engine, main_session, main_db_path, credentials_session
):
    secret = "sk-test-abc123-do-not-leak"

    store = CredentialStore(credentials_session)
    store.set("llm_api_key", secret)
    credentials_session.commit()
    stored_ciphertext = credentials_session.get(Credential, "llm_api_key").ciphertext

    # The main store keeps only settings *metadata* (key + is_secret flag) —
    # the plaintext/ciphertext value itself must never land in this table.
    SettingsRepository(main_session).set_secret_metadata("llm_api_key")
    main_session.commit()

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
            raw = p.read_bytes()
            assert secret.encode() not in raw, p
            # The guarantee is "no credential material in the primary file",
            # not "the material there is encrypted" — the ciphertext must be
            # absent too.
            assert stored_ciphertext not in raw, p


def test_credentials_file_is_ciphertext_only(credentials_session, credentials_db_path, monkeypatch, tmp_path):
    secret = "sk-test-abc123-do-not-leak"

    store = CredentialStore(credentials_session)
    store.set("llm_api_key", secret)
    credentials_session.commit()
    stored_ciphertext = credentials_session.get(Credential, "llm_api_key").ciphertext

    with credentials_session.get_bind().connect() as conn:
        conn.exec_driver_sql("PRAGMA wal_checkpoint(FULL)")

    targets = [
        credentials_db_path,
        Path(str(credentials_db_path) + "-wal"),
        Path(str(credentials_db_path) + "-shm"),
    ]
    found_nonzero_file = False
    found_ciphertext = False
    for p in targets:
        if p.exists():
            raw = p.read_bytes()
            assert secret.encode() not in raw, p
            if stored_ciphertext in raw:
                found_ciphertext = True
            if p == credentials_db_path:
                assert len(raw) > 0
                found_nonzero_file = True
    assert found_nonzero_file
    assert found_ciphertext

    # Without the key, decrypting must raise rather than return plaintext.
    # No-op the dotenv load so a developer's .env cannot re-supply the key.
    monkeypatch.setattr("huntloop.config.dotenv.load_dotenv", lambda *a, **k: False)
    monkeypatch.delenv("HUNTLOOP_SECRET_KEY", raising=False)
    with pytest.raises(ConfigError):
        CredentialStore(credentials_session)


def test_wrong_secret_key_raises_named_error(credentials_session):
    store = CredentialStore(credentials_session)
    store.set("llm_api_key", "sk-test-abc123-do-not-leak")
    credentials_session.commit()

    wrong_key_store = CredentialStore(credentials_session, secret_key=Fernet.generate_key().decode())
    with pytest.raises(CredentialDecryptError):
        wrong_key_store.get("llm_api_key")


def test_credentials_metadata_is_exactly_credentials_table():
    assert set(CredentialsBase.metadata.tables) == {"credentials"}
