import json

import pytest

from app import claim_review, db


def setup_settings_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db.defaults, "settings_db_path", str(tmp_path / "settings.db"))
    monkeypatch.setattr(db.defaults, "learning_db_path", str(tmp_path / "learning.db"))
    db.init_databases()


def test_claim_review_passes_only_when_all_claims_are_supported_and_cited(tmp_path, monkeypatch):
    setup_settings_db(tmp_path, monkeypatch)
    url = "https://docs.python.org/3/library/json.html"
    source = {
        "title": "Python JSON docs",
        "url": url,
        "content": "Invalid JSON input may raise a decoding error. Applications should handle malformed input carefully.",
    }

    def fake_request(_path, payload, **_kwargs):
        assert payload["format"] == "json"
        request = json.loads(payload["messages"][-1]["content"])
        assert request["evidence"][0]["url"] == url
        return {"message": {"content": json.dumps({
            "overall": "supported",
            "claims": [{
                "claim": "Invalid JSON may raise a decoding error.",
                "verdict": "supported",
                "evidence_urls": [url],
                "reason": "Directly stated in the excerpt.",
            }],
        })}}

    monkeypatch.setattr(claim_review, "_request", fake_request)
    result = claim_review.review_answer_claims(
        "Why can JSON parsing fail?",
        f"Invalid JSON may raise a decoding error. Source: {url}",
        [source],
    )
    assert result["passed"] is True
    assert result["reviewer"] == "local_ollama_model_assisted_not_independent"


def test_claim_review_fails_closed_on_malformed_json(tmp_path, monkeypatch):
    setup_settings_db(tmp_path, monkeypatch)
    monkeypatch.setattr(claim_review, "_request", lambda *_args, **_kwargs: {
        "message": {"content": "not JSON"},
    })
    result = claim_review.review_answer_claims(
        "question",
        "A factual answer with a citation https://docs.python.org/",
        [{
            "url": "https://docs.python.org/",
            "title": "Docs",
            "content": "This excerpt contains enough text to be considered usable evidence for a test.",
        }],
    )
    assert result["passed"] is False
    assert result["claims"] == []
    assert result["error"].startswith("review_unavailable_or_malformed:")


def test_claim_review_rejects_unsupported_claims_and_unknown_urls(tmp_path, monkeypatch):
    setup_settings_db(tmp_path, monkeypatch)
    known_url = "https://docs.python.org/3/library/json.html"
    unknown_url = "https://example.invalid/not-retrieved"
    monkeypatch.setattr(claim_review, "_request", lambda *_args, **_kwargs: {
        "message": {"content": json.dumps({
            "overall": "supported",
            "claims": [{
                "claim": "This claim is not in the excerpt.",
                "verdict": "supported",
                "evidence_urls": [unknown_url],
                "reason": "Unsupported assertion.",
            }],
        })},
    })
    result = claim_review.review_answer_claims(
        "question",
        f"Unsupported assertion. Source: {known_url}",
        [{
            "url": known_url,
            "title": "Docs",
            "content": "The excerpt does not establish the claim being tested here.",
        }],
    )
    assert result["passed"] is False
    assert result["error"] == "invalid_claim_fields"



@pytest.mark.parametrize(
    ("answer", "expected"),
    [
        ("Source: https://docs.python.org/3/library/json.html.", True),
        ("Source: <https://docs.python.org/3/library/json.html>", True),
        ('Source: "https://docs.python.org/3/library/json.html"', True),
        ("Source: https://docs.python.org/3/library/json.html.evil.example", False),
        ("Source: https://docs.python.org/3/library/json.html?next=evil", False),
        ("Source: https://docs.python.org/3/library/json.html-extra", False),
    ],
)
def test_citation_must_be_an_exact_url_token(answer, expected):
    url = "https://docs.python.org/3/library/json.html"
    assert claim_review._answer_contains_exact_url(answer, url) is expected


def test_claim_review_does_not_accept_a_misleading_url_substring(tmp_path, monkeypatch):
    setup_settings_db(tmp_path, monkeypatch)
    url = "https://docs.python.org/3/library/json.html"
    monkeypatch.setattr(claim_review, "_request", lambda *_args, **_kwargs: {
        "message": {"content": json.dumps({
            "overall": "supported",
            "claims": [{
                "claim": "Invalid JSON may raise a decoding error.",
                "verdict": "supported",
                "evidence_urls": [url],
                "reason": "Directly stated in the excerpt.",
            }],
        })},
    })
    result = claim_review.review_answer_claims(
        "Why can JSON parsing fail?",
        "Invalid JSON may raise a decoding error. Source: https://docs.python.org/3/library/json.html.evil.example",
        [{
            "title": "Python JSON docs",
            "url": url,
            "content": "Invalid JSON input may raise a decoding error. Applications should handle malformed input carefully.",
        }],
    )
    assert result["passed"] is False
    assert result["error"] == "one_or_more_claims_not_supported_or_cited"
