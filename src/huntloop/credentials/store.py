"""Fernet-backed CredentialStore.

This module is the only place in the codebase permitted to hold plaintext
credential material in memory. The plaintext is never written to the main
store, never logged, and never included in an exception message.
"""

from __future__ import annotations

from datetime import UTC, datetime

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from huntloop.config import get_secret_key
from huntloop.credentials.models import Credential
from huntloop.db.upsert import upsert


class CredentialDecryptError(RuntimeError):
    """Raised when a stored credential cannot be decrypted with the current key."""


class CredentialStore:
    """Encrypt-on-write / decrypt-on-read access to the `credentials` table.

    The secret key is resolved once, at construction time, so a missing or
    malformed `HUNTLOOP_SECRET_KEY` fails fast (via `ConfigError` from
    `get_secret_key()`) rather than lazily on first access.
    """

    def __init__(self, session: Session, secret_key: str | None = None) -> None:
        self.session = session
        key = secret_key if secret_key is not None else get_secret_key()
        self._fernet = Fernet(key.encode())

    def set(self, key: str, plaintext: str) -> None:
        ciphertext = self._fernet.encrypt(plaintext.encode())
        upsert(
            self.session.connection(),
            Credential.__table__,
            index_elements=["key"],
            values={
                "key": key,
                "ciphertext": ciphertext,
                "updated_at": datetime.now(UTC),
            },
            update_columns=["ciphertext", "updated_at"],
        )

    def get(self, key: str) -> str | None:
        row = self.session.get(Credential, key)
        if row is None:
            return None
        try:
            return self._fernet.decrypt(row.ciphertext).decode()
        except InvalidToken as exc:
            raise CredentialDecryptError(
                f"Stored credential {key!r} could not be decrypted — "
                "HUNTLOOP_SECRET_KEY is wrong or was rotated. "
                "Re-enter the credential, or restore the original key."
            ) from exc

    def delete(self, key: str) -> None:
        self.session.execute(delete(Credential).where(Credential.key == key))

    def list_keys(self) -> list[str]:
        return list(self.session.scalars(select(Credential.key).order_by(Credential.key)).all())

    def has(self, key: str) -> bool:
        return self.session.get(Credential, key) is not None


__all__ = ["CredentialDecryptError", "CredentialStore"]
