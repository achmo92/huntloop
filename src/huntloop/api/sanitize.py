"""The UI-06 sanitization boundary.

Employer job descriptions reach HuntLoop as raw HTML from ATS APIs and careers
pages (Phase 2 stores ``description_html or description_plain``). That content
is attacker-influenced: anyone who can post a job can embed a ``<script>`` block
or an inline event handler. Every description field MUST pass through
``to_safe_text`` before it is serialized into an API response — the frontend
renders text nodes only, but this module is the boundary that makes that safe.

The extraction logic reuses ``visible_text`` from
``huntloop.discovery.crawl.careers`` verbatim: Phase 2 already trusts it for
content hashing, it strips ``script``/``style``/``noscript``/``svg`` blocks,
removes the remaining tags, unescapes entities and collapses whitespace. One
extra post-unescape tag sweep is added here — ``visible_text`` unescapes AFTER
stripping tags, so an entity-encoded payload (``&lt;script&gt;``) would
otherwise survive as literal angle brackets.
"""

from __future__ import annotations

import re

from huntloop.discovery.crawl.careers import visible_text


def to_safe_text(raw: str | None) -> str:
    """Return human-readable plain text carrying no executable markup.

    Contract: the result contains no tag shape, no inline event handler and no
    script/style content. ``None`` and empty input both yield ``""`` (missing
    descriptions must never crash a response), and already-plain text is
    returned unchanged (idempotent).
    """
    if not raw:
        return ""
    text = visible_text(raw)
    # Second sweep after entity unescaping — see module docstring.
    text = re.sub(r"<[^>]*>", " ", text)
    return " ".join(text.split())
