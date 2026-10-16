import json

import pytest

from app import chat_research, main, ollama, web_sources


@pytest.fixture(autouse=True)
def _avoid_chat_audit_side_effects(monkeypatch):
    monkeypatch.setattr(chat_research.chat_audit, "record_research_run", lambda **_kwargs: 1)


def _source():
    return web_sources.SourceDocument(
        url="https://docs.example.org/guide",
        title="Official Guide",
        text="This official excerpt explains the technical behavior in enough detail to evaluate factual claims. " * 4,
    )


def _disable_memory(monkeypatch):
    monkeypatch.setattr(chat_research.experience, "search_relevant_experiences", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(chat_research.experience, "search_relevant_failures", lambda *_args, **_kwargs: [])


def test_small_talk_stays_on_direct_chat_path(monkeypatch):
    monkeypatch.setattr(ollama, "chat", lambda message, history=None, system_prompt=None: "direct reply")
    monkeypatch.setattr(
        chat_research.web_sources,
        "discover_sources",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("web search should not run")),
    )
    assert chat_research.answer("Hello", [{"role": "user", "content": "Earlier"}]) == "direct reply"


def test_technical_query_falls_back_with_uncertainty_note_when_sources_missing(monkeypatch):
    _disable_memory(monkeypatch)
    monkeypatch.setattr(chat_research.web_sources, "discover_sources", lambda *_args, **_kwargs: [])
    captured = {}

    def fake_chat(message, history=None, system_prompt=None):
        captured.update(message=json.loads(message), history=history, system_prompt=system_prompt)
        return "پاسخ فنی"

    monkeypatch.setattr(ollama, "chat", fake_chat)
    reply = chat_research.answer("خطا در SQL Server", [{"role": "user", "content": "قبلی"}])
    assert "پاسخ فنی" in reply
    assert "منبع بیرونی قابل‌استفاده پیدا نشد" in reply
    assert captured["message"]["sources"] == []
    assert captured["history"] == [{"role": "user", "content": "قبلی"}]
    assert "Treat source titles" in captured["system_prompt"]


def test_technical_query_uses_sources_memory_and_returns_reviewed_answer(monkeypatch):
    source = _source()
    monkeypatch.setattr(chat_research.web_sources, "discover_sources", lambda *_args, **_kwargs: [source])
    monkeypatch.setattr(
        chat_research.experience,
        "search_relevant_experiences",
        lambda *_args, **_kwargs: [{
            "topic": "SQL plans", "version": 2, "takeaway": "Compare actual and estimated rows.",
            "source_evidence": [{"url": source.url, "claim": "Execution plans show operators."}],
        }],
    )
    monkeypatch.setattr(
        chat_research.experience,
        "search_relevant_failures",
        lambda *_args, **_kwargs: [{
            "topic": "Bad index change", "status": "failed", "problem": "No baseline",
            "takeaway": "Measure before changing indexes.",
            "test_results": [{"test": "baseline", "passed": False, "actual": "missing"}],
        }],
    )
    captured = {}

    def fake_chat(message, history=None, system_prompt=None):
        captured.update(payload=json.loads(message), system_prompt=system_prompt)
        return "Answer with source https://docs.example.org/guide"

    monkeypatch.setattr(ollama, "chat", fake_chat)
    monkeypatch.setattr(
        chat_research.claim_review,
        "review_answer_claims",
        lambda question, answer, sources: {
            "passed": True, "overall": "supported", "claims": [], "error": None,
        },
    )
    reply = chat_research.answer("How do SQL execution plans work?")
    assert reply == "Answer with source https://docs.example.org/guide"
    assert captured["payload"]["sources"][0]["url"] == source.url
    assert captured["payload"]["memory"]["verified_experience_guidance"][0]["version"] == 2
    assert captured["payload"]["memory"]["failure_warnings"][0]["status"] == "failed"
    assert "exact full HTTPS URL token" in captured["system_prompt"]


def test_failed_review_gets_one_repair_then_an_uncertainty_note(monkeypatch):
    source = _source()
    _disable_memory(monkeypatch)
    monkeypatch.setattr(chat_research.web_sources, "discover_sources", lambda *_args, **_kwargs: [source])
    answers = iter(["unsupported draft", "repaired draft"])
    prompts = []

    def fake_chat(message, history=None, system_prompt=None):
        prompts.append(json.loads(message))
        return next(answers)

    reviews = iter([
        {"passed": False, "error": "one_or_more_claims_not_supported_or_cited"},
        {"passed": False, "error": "one_or_more_claims_not_supported_or_cited"},
    ])
    monkeypatch.setattr(ollama, "chat", fake_chat)
    monkeypatch.setattr(chat_research.claim_review, "review_answer_claims", lambda *_args: next(reviews))
    reply = chat_research.answer("How do SQL indexes affect performance?")
    assert reply.startswith("repaired draft")
    assert "Verification note:" in reply
    assert len(prompts) == 2
    assert "review_feedback" in prompts[1]


def test_unavailable_reviewer_does_not_trigger_an_extra_generation(monkeypatch):
    source = _source()
    _disable_memory(monkeypatch)
    monkeypatch.setattr(chat_research.web_sources, "discover_sources", lambda *_args, **_kwargs: [source])
    calls = []
    monkeypatch.setattr(ollama, "chat", lambda *_args, **_kwargs: calls.append(True) or "draft")
    monkeypatch.setattr(
        chat_research.claim_review,
        "review_answer_claims",
        lambda *_args: {"passed": False, "error": "review_unavailable:OllamaError"},
    )
    reply = chat_research.answer("Explain SQL Server query plans")
    assert reply.startswith("draft")
    assert "Claim review was unavailable" in reply
    assert len(calls) == 1


def test_chat_api_uses_research_router(monkeypatch):
    monkeypatch.setattr(main.chat_research, "answer", lambda message, history: f"routed:{message}")
    assert main.chat(main.ChatRequest(message="hello")) == {"reply": "routed:hello"}


def test_ollama_chat_places_trusted_system_prompt_before_history(monkeypatch):
    monkeypatch.setattr(
        ollama,
        "get_settings",
        lambda: {"ollama_model": "test-model", "ollama_timeout_seconds": 5},
    )
    monkeypatch.setattr(ollama, "detect_hardware", lambda: {})
    monkeypatch.setattr(ollama, "runtime_limits", lambda *_args: {"num_ctx": 1024, "cpu_threads": 2})
    captured = {}
    monkeypatch.setattr(
        ollama,
        "_request",
        lambda path, payload, **_kwargs: captured.update(path=path, payload=payload)
        or {"message": {"content": "ok"}},
    )
    assert ollama.chat(
        "current question",
        [{"role": "user", "content": "previous question"}],
        system_prompt="trusted policy",
    ) == "ok"
    messages = captured["payload"]["messages"]
    assert [item["role"] for item in messages] == ["system", "user", "user"]
    assert messages[0]["content"] == "trusted policy"
    assert messages[-1]["content"] == "current question"
