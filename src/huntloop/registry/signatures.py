"""ATS detection signatures and HTML sweep function (REG-02).

Patterns are copied verbatim from 02-RESEARCH.md's live-verified table.  Two
cases are live-reproduced and name-checked in the test suite:

- cobalt.io: Greenhouse, slug ``cobaltio`` — from a standard job-board link.
- NewRocket:  Greenhouse, slug ``highmetric`` — from the inline XHR string
  ``boards-api.greenhouse.io/v1/boards/highmetric/jobs`` in static HTML.
  The page has zero ``job-boards.greenhouse.io`` links; only the
  ``direct_api_call`` pattern finds it.  This is REG-03.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass(frozen=True)
class SlugCandidate:
    platform: str
    slug: str | None
    weak: bool = False
    matched_patterns: tuple[str, ...] = ()
    source: str = "static_html"  # static_html | rendered_html | name_guess


# ---------------------------------------------------------------------------
# Strong signatures
# ---------------------------------------------------------------------------
# (regex, label) pairs.  All patterns compiled with re.IGNORECASE at sweep time.
# Copied verbatim from 02-RESEARCH.md's live-verified table.

SIGNATURES: dict[str, tuple[tuple[str, str], ...]] = {
    "greenhouse": (
        # boards.greenhouse.io now 301s to job-boards.greenhouse.io, but employer
        # HTML still contains the old string.
        (r"boards\.greenhouse\.io/([\w-]+)", "legacy_hosted_board"),
        (r"job-boards(?:\.\w+)?\.greenhouse\.io/([\w-]+)", "hosted_board"),
        # The NewRocket case. Without this pattern a page whose jobs render
        # client-side is misclassified as "needs JS" and routed to the expensive
        # rendered pass — or missed entirely. 02-RESEARCH.md reproduced this live.
        (r"boards-api\.greenhouse\.io/v1/boards/([\w-]+)/jobs", "direct_api_call"),
        (r"embed/job_board(?:/js)?\?for=([\w-]+)", "embed_script"),
    ),
    "lever": (
        (r"jobs\.lever\.co/([\w-]+)", "hosted_board"),
        (r"api\.lever\.co/v0/postings/([\w-]+)", "direct_api_call"),
    ),
    "ashby": (
        # Order matters: /embed and __ashbyBaseJobBoardUrl checked BEFORE the bare
        # hosted_board pattern so that `acme/embed` captures the correct label.
        (r"jobs\.ashbyhq\.com/([\w-]+)/embed", "embed_script"),
        (r"__ashbyBaseJobBoardUrl\s*=\s*['\"]https://jobs\.ashbyhq\.com/([\w-]+)", "embed_config"),
        (r"api\.ashbyhq\.com/posting-api/job-board/([\w-]+)", "direct_api_call"),
        (r"jobs\.ashbyhq\.com/([\w-]+)", "hosted_board"),
        # Bare domain variant seen in some employer pages (e.g. ramp_careers fixture).
        (r"ashbyhq\.com/([\w-]+)/jobs", "hosted_board_bare"),
    ),
}

# Weak patterns: confirm the platform but cannot extract a slug.
WEAK_SIGNATURES: dict[str, tuple[str, ...]] = {
    "greenhouse": (r"gh_jid=",),  # platform confirmed, slug unknown -> escalate to Tier 3
}

# Slugs that look like slugs but are not employer identifiers.
SLUG_BLOCKLIST = {"embed", "js", "jobs", "board", "api", "v1", "static", "assets"}

_COMPILED: dict[str, list[tuple[re.Pattern, str]]] = {
    platform: [(re.compile(pat, re.IGNORECASE), label) for pat, label in pairs]
    for platform, pairs in SIGNATURES.items()
}

_WEAK_COMPILED: dict[str, list[re.Pattern]] = {
    platform: [re.compile(pat, re.IGNORECASE) for pat in pats]
    for platform, pats in WEAK_SIGNATURES.items()
}


def sweep_signatures(
    html: str,
    *,
    source: str = "static_html",
) -> tuple[SlugCandidate, ...]:
    """Sweep ``html`` for ATS detection patterns and return deduplicated candidates.

    The sweep is case-insensitive.  A single ``(platform, slug)`` pair may match
    multiple patterns; all label strings accumulate in ``matched_patterns``.

    Ordering: strong candidates (``weak=False``) come before weak ones.  Within
    each group, candidates are sorted by ``len(matched_patterns)`` descending (more
    evidence first), then ``slug`` ascending (deterministic tie-break).

    Weak candidates: only appended for a platform when no strong candidate for that
    platform was found in the HTML.
    """
    # (platform, slug) -> set of labels matched
    strong: dict[tuple[str, str], set[str]] = {}

    for platform, compiled_patterns in _COMPILED.items():
        for compiled, label in compiled_patterns:
            for match in compiled.finditer(html):
                slug = match.group(1).lower()
                if len(slug) < 2 or slug in SLUG_BLOCKLIST:
                    continue
                key = (platform, slug)
                strong.setdefault(key, set()).add(label)

    strong_candidates: list[SlugCandidate] = [
        SlugCandidate(
            platform=platform,
            slug=slug,
            weak=False,
            matched_patterns=tuple(sorted(labels)),
            source=source,
        )
        for (platform, slug), labels in strong.items()
    ]
    strong_candidates.sort(key=lambda c: (-len(c.matched_patterns), c.slug or ""))

    # Weak: only when the platform has no strong match.
    platforms_with_strong = {c.platform for c in strong_candidates}
    weak_candidates: list[SlugCandidate] = []
    for platform, compiled_pats in _WEAK_COMPILED.items():
        if platform in platforms_with_strong:
            continue
        for compiled in compiled_pats:
            if compiled.search(html):
                weak_candidates.append(
                    SlugCandidate(
                        platform=platform,
                        slug=None,
                        weak=True,
                        matched_patterns=("gh_jid",),
                        source=source,
                    )
                )
                break  # one weak candidate per platform is enough

    return tuple(strong_candidates + weak_candidates)
