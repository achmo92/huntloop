"""Credentials-store ORM model. Lives in its own database file, on its own Base."""

from datetime import UTC, datetime

from sqlalchemy import DateTime, LargeBinary, Text
from sqlalchemy.orm import Mapped, mapped_column

from huntloop.credentials.base import CredentialsBase


class Credential(CredentialsBase):
    """One encrypted secret, addressed by the same string key the main
    `settings` table records as metadata.

    The main store's `settings` row for this key carries `value = NULL` and
    `is_secret = True`; the ciphertext exists ONLY here. That is what makes a
    `cp data/huntloop.db backup.db` carry zero credential material, encrypted
    or otherwise (01-CONTEXT.md, Credential Storage).
    """

    __tablename__ = "credentials"

    key: Mapped[str] = mapped_column(Text, primary_key=True)  # e.g. "llm_api_key"
    ciphertext: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
        nullable=False,
    )


__all__ = ["Credential"]
