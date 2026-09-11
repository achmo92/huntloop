"""RUN-08. Run-level USD spend cap, enforced before each model call.

The cap is checked BEFORE a call, never after: the locked decision in
03-CONTEXT.md is that the run stops before spending past the cap, not that it
reports having overshot. Overshoot by at most one in-flight call is possible
(the call already issued when another thread trips the cap) and is accepted.

Thread-safe by construction. LangGraph's synchronous invoke fans employers out
across a real ThreadPoolExecutor, so this accumulator is mutated from several
OS threads at once. CPython's GIL does not make `self._spent += x` atomic in
general, and a spend CAP is not the place to rely on "usually".
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from decimal import Decimal

from huntloop.llm.client import LlmUsage
from huntloop.pricing.table import COST_QUANTUM, cost_for_usage, is_priced


class SpendCapReached(RuntimeError):
    """The configured per-run USD spend cap was reached before this model call.

    Raised by SpendTracker.check(). Deliberately NOT caught inside
    score_listing: a cap-blocked listing must produce no ScoredListing at all,
    because every ScoredListing reaching write_batch is written unconditionally
    (TRAK-06), and the locked decision is that cap-blocked listings are held
    back and retried on the next run, not persisted unscored.
    """


@dataclass
class SpendTracker:
    """Accumulates a run's token usage and USD spend, and gates further calls."""

    cap_usd: Decimal | None = None
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _spent_usd: Decimal = field(default_factory=lambda: Decimal("0"))
    _tokens_in: int = 0
    _tokens_out: int = 0
    _unpriced_models: set[str] = field(default_factory=set)

    @property
    def spent_usd(self) -> Decimal:
        with self._lock:
            return self._spent_usd.quantize(COST_QUANTUM)

    @property
    def tokens_in(self) -> int:
        with self._lock:
            return self._tokens_in

    @property
    def tokens_out(self) -> int:
        with self._lock:
            return self._tokens_out

    @property
    def unpriced_models(self) -> frozenset[str]:
        with self._lock:
            return frozenset(self._unpriced_models)

    def check(self) -> None:
        """Raise SpendCapReached if the cap is already met. Call BEFORE a model call."""
        if self.cap_usd is None:
            return
        with self._lock:
            if self._spent_usd >= self.cap_usd:
                raise SpendCapReached(self._reason_locked())

    def record(self, usage: LlmUsage | None) -> None:
        """Fold one completed model call's usage into the run totals."""
        if usage is None:
            return
        cost = cost_for_usage(usage.model, usage.prompt_tokens, usage.completion_tokens)
        with self._lock:
            self._tokens_in += usage.prompt_tokens
            self._tokens_out += usage.completion_tokens
            self._spent_usd += cost
            if usage.model and not is_priced(usage.model):
                self._unpriced_models.add(usage.model)

    def reason(self) -> str:
        with self._lock:
            return self._reason_locked()

    def _reason_locked(self) -> str:
        return (
            f"stopped: spend cap of ${self.cap_usd:.2f} reached after "
            f"${self._spent_usd:.2f} of model spend"
        )
