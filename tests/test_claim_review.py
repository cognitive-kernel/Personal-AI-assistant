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
            "source_conflicts": [],
            "claims": [{
                "claim": "Invalid JSON may raise a decoding error.",
                "verdict": "supported",
                "evidence_urls": [url],
                "reason": "Directly stated in the excerpt.",
            }],
        })}}

    def fake_llm_complete(_task, messages, options=None, response_format=None, **_kwargs):
        return fake_request("/api/chat", {"format": response_format, "messages": messages, "options": options})

    monkeypatch.setattr(claim_review, "llm_complete", fake_llm_complete)
    result = claim_review.review_answer_claims(
        "Why can JSON parsing fail?",
        f"Invalid JSON may raise a decoding error. Source: {url}",
        [source],
    )
    assert result["passed"] is True
    assert result["reviewer"] == "local_ollama_model_assisted_not_independent"


def test_claim_review_fails_closed_on_malformed_json(tmp_path, monkeypatch):
    setup_settings_db(tmp_path, monkeypatch)
    monkeypatch.setattr(claim_review, "llm_complete", lambda *_args, **_kwargs: {
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
    monkeypatch.setattr(claim_review, "llm_complete", lambda *_args, **_kwargs: {
        "message": {"content": json.dumps({
            "overall": "supported",
            "source_conflicts": [],
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
    monkeypatch.setattr(claim_review, "llm_complete", lambda *_args, **_kwargs: {
        "message": {"content": json.dumps({
            "overall": "supported",
            "source_conflicts": [],
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


def test_claim_review_surfaces_source_conflict_and_fails_closed(tmp_path, monkeypatch):
    setup_settings_db(tmp_path, monkeypatch)
    first_url = "https://docs.example.org/version-a"
    second_url = "https://docs.example.org/version-b"

    def fake_request(_path, payload, **_kwargs):
        request = json.loads(payload["messages"][-1]["content"])
        assert "Compare the supplied sources against each other" in request["task"]
        return {"message": {"content": json.dumps({
            "overall": "supported",
            "claims": [{
                "claim": "The feature is enabled by default.",
                "verdict": "supported",
                "evidence_urls": [first_url],
                "reason": "The first excerpt states it is enabled.",
            }],
            "source_conflicts": [{
                "topic": "Default feature state",
                "source_a_url": first_url,
                "source_b_url": second_url,
                "description": "The first source says enabled; the second says disabled.",
            }],
        })}}

    monkeypatch.setattr(claim_review, "llm_complete", lambda _task, messages, options=None, response_format=None, **_kwargs: fake_request("/api/chat", {"messages": messages, "options": options, "format": response_format}))
    result = claim_review.review_answer_claims(
        "What is the default state?",
        f"The feature is enabled. Source: {first_url}",
        [
            {"url": first_url, "title": "Version A", "content": "The feature is enabled by default in this release."},
            {"url": second_url, "title": "Version B", "content": "The feature is disabled by default in this release."},
        ],
    )
    assert result["passed"] is False
    assert result["overall"] == "insufficient_evidence"
    assert result["error"] == "source_conflicts_require_human_review"
    assert result["source_conflicts"][0]["source_a_url"] == first_url
    assert result["source_conflicts"][0]["source_b_url"] == second_url


@pytest.mark.parametrize(
    "conflict",
    [
        {"topic": "x", "source_a_url": "https://unknown.example/a", "source_b_url": "https://docs.example.org/b", "description": "Conflict."},
        {"topic": "x", "source_a_url": "https://docs.example.org/a", "source_b_url": "https://docs.example.org/a", "description": "Conflict."},
        {"topic": "", "source_a_url": "https://docs.example.org/a", "source_b_url": "https://docs.example.org/b", "description": "Conflict."},
    ],
)
def test_claim_review_rejects_invalid_source_conflict_metadata(tmp_path, monkeypatch, conflict):
    setup_settings_db(tmp_path, monkeypatch)
    first_url = "https://docs.example.org/a"
    second_url = "https://docs.example.org/b"
    monkeypatch.setattr(claim_review, "llm_complete", lambda *_args, **_kwargs: {
        "message": {"content": json.dumps({
            "overall": "supported",
            "claims": [{
                "claim": "The statement is supported.",
                "verdict": "supported",
                "evidence_urls": [first_url],
                "reason": "The first excerpt directly supports it.",
            }],
            "source_conflicts": [conflict],
        })},
    })
    result = claim_review.review_answer_claims(
        "Question?",
        f"The statement is supported. Source: {first_url}",
        [
            {"url": first_url, "title": "A", "content": "The first excerpt directly supports this test claim."},
            {"url": second_url, "title": "B", "content": "The second excerpt has different information in it."},
        ],
    )
    assert result["passed"] is False
    assert result["error"] == "invalid_source_conflict_fields"


def test_claim_review_fails_closed_when_source_conflict_field_is_missing(tmp_path, monkeypatch):
    setup_settings_db(tmp_path, monkeypatch)
    url = "https://docs.example.org/guide"
    monkeypatch.setattr(claim_review, "llm_complete", lambda *_args, **_kwargs: {
        "message": {"content": json.dumps({
            "overall": "supported",
            "claims": [{
                "claim": "The excerpt supports this statement.",
                "verdict": "supported",
                "evidence_urls": [url],
                "reason": "Direct support.",
            }],
        })},
    })
    result = claim_review.review_answer_claims(
        "Question?",
        f"The excerpt supports this statement. Source: {url}",
        [{"url": url, "title": "Guide", "content": "The excerpt supports this statement with enough text to be usable."}],
    )
    assert result["passed"] is False
    assert result["error"] == "missing_source_conflicts"
