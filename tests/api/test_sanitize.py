"""UI-06 sanitization boundary tests.

`to_safe_text` is the ONLY place employer-supplied description text crosses
the API boundary. These are adversarial fixtures: a browser must never see a
`<script>` block, a tag name, or an inline event handler. The import is
deliberately inside the test helper so the RED phase fails at run time
(ImportError on each test), not at collection time — matching 04-03's
router-request-time RED convention.
"""


def _sanitize(raw):
    from huntloop.api.sanitize import to_safe_text

    return to_safe_text(raw)


def test_strips_script_block_and_unwraps_inline_tags():
    out = _sanitize("<script>alert(1)</script><p>Senior <b>Backend</b> Engineer</p>")
    assert "Senior Backend Engineer" in out
    assert "<" not in out
    assert "alert(1)" not in out
    assert "script" not in out


def test_strips_inline_event_handler_attribute():
    out = _sanitize("<img src=x onerror=alert(2)>Apply now")
    assert "Apply now" in out
    assert "onerror" not in out
    assert "<img" not in out


def test_plain_text_is_idempotent():
    assert _sanitize("plain text already") == "plain text already"


def test_empty_and_none_never_crash():
    assert _sanitize("") == ""
    assert _sanitize(None) == ""
