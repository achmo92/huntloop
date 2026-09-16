"""LOOP-02 / D-07. General, unattached feedback. Consumed at proposal-generation
time by huntloop.loop.rationale; it never bypasses the LOOP-04 threshold.

This is the freeform entry point on the proposal review surface (D-07): the one
page owns both "give feedback" and "review what came of it". A general note is
``FeedbackNote(job_id=NULL, source=FeedbackSource.CHAT)`` — deliberately
unattached, because this endpoint never infers a listing.

The per-listing note path (``POST /api/jobs/{id}/notes``, TRAK-02) is a separate,
already-shipped concept with ``source=JOB_NOTE``. It is untouched here: nothing
in this module imports from or generalises ``jobs.py``.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from huntloop.api.deps import get_session
from huntloop.api.sanitize import to_safe_text
from huntloop.db.models import FeedbackNote, FeedbackSource

router = APIRouter(prefix="/api/feedback", tags=["feedback"])

MAX_FEEDBACK_CHARS = 4000


# ---------------------------------------------------------------------------
# Request / response models (colocated here — mirrors jobs/criteria)
# ---------------------------------------------------------------------------


class FeedbackRequest(BaseModel):
    text: str = Field(min_length=1, max_length=MAX_FEEDBACK_CHARS)


class FeedbackOut(BaseModel):
    id: uuid.UUID
    created_at: datetime


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------


@router.post("", status_code=201, response_model=FeedbackOut)
def create_feedback(
    body: FeedbackRequest, session: Session = Depends(get_session)
) -> FeedbackOut:
    """Store one general feedback note (LOOP-02, D-07).

    Text crosses the boundary through ``to_safe_text`` exactly as ``user_notes``
    does in ``jobs.py``. Pydantic's ``min_length`` does not catch whitespace-only
    input, so the empty check happens after sanitising and stripping.
    """
    text = to_safe_text(body.text).strip()
    if not text:
        raise HTTPException(status_code=422, detail="Feedback text cannot be empty.")

    note = FeedbackNote(job_id=None, text=text, source=FeedbackSource.CHAT)
    session.add(note)
    session.commit()
    session.refresh(note)
    return FeedbackOut(id=note.id, created_at=note.created_at)
