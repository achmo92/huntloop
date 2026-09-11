"""Bounded daemon-thread runner for background API work (Phase 4).

Resolution probes take seconds-to-tens-of-seconds, so ``POST .../resolve``
accepts the request (202) and runs the probe on a daemon thread. The Phase 2
resolution path is imported at MODULE TOP-LEVEL deliberately: tests pin the
seam by monkeypatching ``huntloop.api.background.resolve_employer``, which only
works if the name resolves in this module's namespace at call time. A lazy
import inside the function would instead require patching the source module.

``run_in_background`` is the shared runner plan 04-05's run trigger reuses.
"""

from __future__ import annotations

import threading
import uuid

import httpx

from huntloop.db.base import get_engine, make_session_factory
from huntloop.db.models import Company
from huntloop.discovery.fetch.page import RenderedPageFetcher, StaticPageFetcher
from huntloop.registry.resolve import persist_resolution, resolve_employer


def run_in_background(fn, *args, **kwargs) -> threading.Thread:
    """Start a daemon thread running ``fn(*args, **kwargs)`` and return it.

    Daemon=True so a lingering probe never blocks process shutdown. Callers
    that need determinism in tests capture the returned thread and join it
    with an explicit timeout.
    """
    thread = threading.Thread(target=fn, args=args, kwargs=kwargs, daemon=True)
    thread.start()
    return thread


def resolve_company_in_background(company_id: uuid.UUID) -> None:
    """Resolve one employer via the Phase 2 path on a session of its own.

    The request's session is closed the moment the request returns, so this
    opens its own via ``make_session_factory(get_engine())``, then calls
    ``resolve_employer`` and ``persist_resolution`` — the identical code path
    the CLI uses, never a reimplementation — commits, and always closes both
    the HTTP client and the session.
    """
    session = make_session_factory(get_engine())()
    client = httpx.Client(timeout=10.0, follow_redirects=True)
    try:
        company = session.get(Company, company_id)
        if company is None:
            return
        result = resolve_employer(
            name=company.name,
            careers_url=company.careers_url,
            client=client,
            static_fetcher=StaticPageFetcher(),
            rendered_fetcher=RenderedPageFetcher(),
        )
        persist_resolution(session, company.name, result)
        session.commit()
    finally:
        client.close()
        session.close()
