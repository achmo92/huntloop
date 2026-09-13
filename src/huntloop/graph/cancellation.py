"""GAP-4: cooperative cancellation for a manually stopped run.

A stop is requested from a *different process* than the one running the
pipeline (the API daemon thread or the scheduler), so the marker is DB-level: a
single ``Setting`` row, keyed by ``STOP_REQUEST_SETTING_KEY``, whose value is
the string run id. The run path reads it at employer/stage boundaries and
raises :class:`RunStoppedByUser`, which the graph is configured never to retry
and which escapes the DISC-03 broad except on purpose.

This mirrors the Phase 3 spend-cap precedent: an interruption is a first-class
fact about the run (a terminal ``STOPPED`` status), not a disguised failure.
"""

from __future__ import annotations

import uuid

from sqlalchemy.orm import Session, sessionmaker

from huntloop.db.models import Setting
from huntloop.db.repository import SettingsRepository

# The Setting key holding the run id whose stop has been requested.
STOP_REQUEST_SETTING_KEY = "run_stop_request"


class RunStoppedByUser(RuntimeError):
    """A stop was requested for this run and observed at a safe boundary.

    Deliberately a ``RuntimeError`` subclass so it is caught by generic
    exception handling only where that is intended — the node and the entry
    point re-raise it explicitly before their broad ``except Exception``.
    """

    def __init__(self, message: str = "stopped by user request") -> None:
        super().__init__(message)


def request_stop(session: Session, run_id: uuid.UUID | str) -> None:
    """Record that ``run_id`` should stop at its next boundary.

    Written through :class:`SettingsRepository` with the same upsert
    discipline plan 04-06 uses for UI-editable settings. The caller owns the
    commit: the API endpoint commits with its 202 response so the daemon thread
    observes the marker.
    """
    SettingsRepository(session).set_value(STOP_REQUEST_SETTING_KEY, str(run_id))


def is_stop_requested(sessionmaker: sessionmaker, run_id: uuid.UUID | str) -> bool:
    """True when the pending stop names exactly this run.

    Opens its own session: the check runs inside per-employer graph branches
    that must not share a session (SQLAlchemy sessions are not thread-safe).
    A marker naming a different run (a stale request) does not stop this one.
    """
    session = sessionmaker()
    try:
        value = SettingsRepository(session).get_value(STOP_REQUEST_SETTING_KEY)
        return value is not None and str(value) == str(run_id)
    finally:
        session.close()


def clear_stop_request(session: Session) -> None:
    """Remove the marker once a stop has been honored (or superseded).

    Clearing is what keeps the scheduler's overlap guard clear for the next
    fire: the RUNNING Run row is the in-progress marker, and the stop marker
    must not linger to affect a later run.
    """
    setting = session.get(Setting, STOP_REQUEST_SETTING_KEY)
    if setting is not None:
        session.delete(setting)
