"""SCOR-05. This stage exists to reduce cost, so it runs on the cheap model and sends a
truncated posting. It is also the most dangerous stage in the pipeline: a wrong drop is
invisible to the user forever. Hence the fail-open bias and the recorded reason on every
verdict.
"""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict

from huntloop.config import load_config
from huntloop.llm.client import LlmCall, LlmResponseError, LlmUsage, complete_json
from huntloop.scoring.config import TRIAGE_PROMPT

# Triage decides "is this plausibly relevant"; the first ~2k chars carry the role
# summary.  Sending the full posting multiplies the cost of the stage that exists
# to reduce cost.
MAX_TRIAGE_DESCRIPTION_CHARS = 2000


class TriagePayload(BaseModel):
    model_config = ConfigDict(extra="ignore")
    keep: bool
    reason: str = ""


@dataclass(frozen=True)
class TriageVerdict:
    keep: bool
    reason: str
    model: str
    errored: bool = False
    usage: LlmUsage | None = None


def build_triage_user_message(listing, criteria) -> str:
    """Build the per-listing user message for the triage call.

    ALL per-listing content goes here and NEVER into the system prompt.  The
    system prompt is a hash input for SCOR-10 and must be byte-identical across
    every call; any per-listing f-string creeping in would invalidate the hash.
    """
    description = (listing.description_plain or "")[:MAX_TRIAGE_DESCRIPTION_CHARS]
    return (
        f"TITLE: {listing.title}\n"
        f"LOCATION: {listing.location_raw or 'unspecified'}\n"
        f"POSTED: {listing.posted_at or 'unknown'}\n"
        f"DESCRIPTION: {description}\n"
        f"\n"
        f"CANDIDATE PROFILE: {criteria.profile_summary}\n"
        f"TARGET SENIORITY: {criteria.seniority_min or 'any'} to {criteria.seniority_max or 'any'}"
    )


def triage_listing(client, listing, criteria, *, model: str | None = None) -> TriageVerdict:
    """Run the cheap triage model call on one listing.

    Fail-open bias: every failure mode (network, schema, provider quirk) returns
    ``keep=True`` with the error captured in ``reason``.  Cost of a wrong keep:
    one scoring call.  Cost of a wrong drop: the user never sees the job.

    Args:
        client: An openai.OpenAI (or protocol-compatible) client.
        listing: A RawListing.
        criteria: A CriteriaPayload.
        model: Override the triage model from config (for tests).
    """
    model = model or load_config().triage_model

    # Deliberately broad. Every failure mode of this stage — network, schema,
    # provider quirk — has the same correct answer: keep the listing and pay for
    # one scoring call. An exception escaping here would abort the listing
    # entirely, which is the one outcome worse than a wasted call.
    try:
        call: LlmCall = complete_json(
            client,
            model=model,
            system=TRIAGE_PROMPT,
            user=build_triage_user_message(listing, criteria),
            schema=TriagePayload,
            temperature=0.0,
            max_tokens=200,
        )
    except Exception as exc:
        return TriageVerdict(
            keep=True,
            reason=f"triage failed, kept by default: {exc}",
            model=model,
            errored=True,
        )

    keep = bool(call.content.get("keep", True))
    reason = call.content.get("reason") or "no reason given"

    return TriageVerdict(
        keep=keep,
        reason=reason,
        model=model,
        usage=call.usage,
    )
