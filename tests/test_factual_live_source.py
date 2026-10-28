import hashlib
from types import SimpleNamespace

import pytest

from app import factual_verification_tasks, web_sources


def test_live_source_excerpt_is_checked_against_current_official_page(monkeypatch):
    page = "Python documentation: A generator is an iterator that yields values."
    calls = []

    def fake_fetch(url, title_hint, *, max_chars):
        calls.append((url, title_hint, max_chars))
        return SimpleNamespace(url=url, title="Python", text=page)

    monkeypatch.setattr(web_sources, "fetch_public_document", fake_fetch)
    digest = factual_verification_tasks._verify_live_source_excerpt(
        "https://docs.python.org/3.13/reference/expressions.html",
        "A generator is an iterator that yields values.",
    )
    assert digest == hashlib.sha256(page.encode("utf-8")).hexdigest()
    assert calls == [(
        "https://docs.python.org/3.13/reference/expressions.html",
        "factual claim evidence",
        100000,
    )]


def test_live_source_excerpt_fails_closed_when_page_changed(monkeypatch):
    monkeypatch.setattr(
        web_sources,
        "fetch_public_document",
        lambda *args, **kwargs: SimpleNamespace(text="The current page no longer contains the quoted statement."),
    )
    with pytest.raises(ValueError, match="absent from the current live source page"):
        factual_verification_tasks._verify_live_source_excerpt(
            "https://docs.python.org/3.13/reference/expressions.html",
            "A generator is an iterator that yields values.",
        )


def test_live_source_verification_rejects_unallowlisted_hosts_before_fetch(monkeypatch):
    def unexpected_fetch(*args, **kwargs):
        raise AssertionError("unallowlisted host must not be fetched")

    monkeypatch.setattr(web_sources, "fetch_public_document", unexpected_fetch)
    with pytest.raises(ValueError, match="allow-listed official HTTPS"):
        factual_verification_tasks._verify_live_source_excerpt(
            "https://example.com/reference.html",
            "Some documented behavior.",
        )
