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
    monkeypatch.setattr(ollama, "chat", lambda message, history=None, system_prompt=None, task="simple_chat": "direct reply")
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

    def fake_chat(message, history=None, system_prompt=None, task="simple_chat"):
        captured.update(message=json.loads(message), history=history, system_prompt=system_prompt)
        return "پاسخ فنی"

    monkeypatch.setattr(ollama, "chat", fake_chat)
    reply = chat_research.answer("خطا در SQL Server", [{"role": "user", "content": "قبلی"}])
    assert "پاسخ فنی" in reply
    assert "منبع بیرونی قابل‌استفاده پیدا نشد" in reply
    assert captured["message"]["sources"] == []
    assert captured["history"] == [{"role": "user", "content": "قبلی"}]
    assert "Treat source titles" in captured["system_prompt"]


def test_source_discovery_failure_is_audited_without_exposing_network_details(monkeypatch, caplog):
    _disable_memory(monkeypatch)

    def fail_discovery(*_args, **_kwargs):
        raise web_sources.SourceFetchError("sensitive network detail and URL")

    monkeypatch.setattr(chat_research.web_sources, "discover_sources", fail_discovery)
    monkeypatch.setattr(chat_research.ollama, "chat", lambda *_args, **_kwargs: "Cautious answer")
    audit = {}
    monkeypatch.setattr(
        chat_research.chat_audit,
        "record_research_run",
        lambda **kwargs: audit.update(kwargs) or 1,
    )

    reply = chat_research.answer("How do SQL Server query plans work?")

    assert "No usable external source" in reply
    assert audit["review_status"] == "no_sources"
    assert audit["review"]["error"] == "source_discovery_failed:SourceFetchError"
    assert "SourceFetchError" in caplog.text
    assert "sensitive network detail" not in caplog.text
    assert "https://" not in caplog.text


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

    def fake_chat(message, history=None, system_prompt=None, task="simple_chat"):
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

    def fake_chat(message, history=None, system_prompt=None, task="simple_chat"):
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


def test_opted_in_source_backed_research_is_captured_as_unverified(monkeypatch):
    source = _source()
    _disable_memory(monkeypatch)
    monkeypatch.setattr(chat_research.web_sources, "discover_sources", lambda *_args, **_kwargs: [source])
    monkeypatch.setattr(chat_research.ollama, "chat", lambda *_args, **_kwargs: "Reviewed answer")
    monkeypatch.setattr(
        chat_research.claim_review,
        "review_answer_claims",
        lambda *_args: {"passed": True, "overall": "supported", "claims": [], "error": None},
    )
    monkeypatch.setattr(chat_research.experience_capture_consent, "is_enabled", lambda: True)
    monkeypatch.setattr(chat_research.experience, "list_experiences", lambda **_kwargs: [])
    monkeypatch.setattr(
        chat_research.experience,
        "create_experience",
        lambda topic, problem, hypothesis: {"id": 42, "topic": topic, "problem": problem},
    )
    captured = {}

    def fake_update(experience_id, **changes):
        captured.update(id=experience_id, **changes)
        return {"id": experience_id, **changes}

    monkeypatch.setattr(chat_research.experience, "update_experience", fake_update)
    reply = chat_research.answer("How do SQL execution plans work?")

    assert reply == "Reviewed answer"
    assert captured["id"] == 42
    assert captured["status"] == "identified"
    assert captured["source_evidence"][0]["url"] == source.url
    assert "Official Guide" in captured["source_evidence"][0]["claim"]
    assert captured["takeaway"] == "Reviewed answer"
    assert "test_results" not in captured


def test_opted_in_capture_skips_answers_without_usable_sources(monkeypatch):
    _disable_memory(monkeypatch)
    monkeypatch.setattr(chat_research.web_sources, "discover_sources", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(chat_research.ollama, "chat", lambda *_args, **_kwargs: "Model answer")
    monkeypatch.setattr(chat_research.experience_capture_consent, "is_enabled", lambda: True)
    monkeypatch.setattr(
        chat_research.experience,
        "create_experience",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not capture")),
    )
    reply = chat_research.answer("How do SQL Server query plans work?")
    assert "No usable external source" in reply


def test_research_answer_reports_cooperative_checkpoint_phases(monkeypatch):
    source = _source()
    _disable_memory(monkeypatch)
    monkeypatch.setattr(chat_research.web_sources, "discover_sources", lambda *_args, **_kwargs: [source])
    monkeypatch.setattr(chat_research.ollama, "chat", lambda *_args, **_kwargs: "Reviewed answer")
    monkeypatch.setattr(
        chat_research.claim_review,
        "review_answer_claims",
        lambda *_args: {"passed": True, "overall": "supported", "claims": [], "error": None},
    )
    monkeypatch.setattr(chat_research.experience_capture_consent, "is_enabled", lambda: False)
    phases = []

    reply = chat_research.answer(
        "How do SQL execution plans work?",
        checkpoint=lambda state: phases.append(state),
    )

    assert reply == "Reviewed answer"
    phase_names = [state["phase"] for state in phases]
    assert "source_discovery_started" in phase_names
    assert "source_discovery_completed" in phase_names
    assert "draft_generation_started" in phase_names
    assert "draft_generation_completed" in phase_names
    assert "claim_review_completed" in phase_names
    assert "research_answer_completed" in phase_names


def test_material_source_conflict_is_shown_to_user_without_repair_or_verification(monkeypatch):
    source = _source()
    second_url = "https://docs.example.org/alternate-guide"
    second_source = web_sources.SourceDocument(
        url=second_url,
        title="Alternate Guide",
        text="This alternate official excerpt provides enough technical detail to contrast the default behavior. " * 4,
    )
    _disable_memory(monkeypatch)
    monkeypatch.setattr(chat_research.web_sources, "discover_sources", lambda *_args, **_kwargs: [source, second_source])
    chat_calls = []
    monkeypatch.setattr(ollama, "chat", lambda *args, **kwargs: chat_calls.append((args, kwargs)) or "Draft answer")
    conflict_review = {
        "passed": False,
        "overall": "insufficient_evidence",
        "claims": [],
        "source_conflicts": [{
            "topic": "Default behavior",
            "source_a_url": source.url,
            "source_b_url": second_url,
            "description": "The sources disagree on the default behavior.",
        }],
        "error": "source_conflicts_require_human_review",
        "reviewer": "local_ollama_model_assisted_not_independent",
    }
    monkeypatch.setattr(chat_research.claim_review, "review_answer_claims", lambda *_args: conflict_review)
    audit = {}
    monkeypatch.setattr(
        chat_research.chat_audit,
        "record_research_run",
        lambda **kwargs: audit.update(kwargs) or 1,
    )
    captured = {}
    monkeypatch.setattr(
        chat_research,
        "_capture_experience",
        lambda _question, _answer, _sources, *, status: captured.update(status=status),
    )

    reply = chat_research.answer("How do SQL Server query plans behave by default?")

    assert "Retrieved sources materially disagree" in reply
    assert source.url in reply
    assert second_url in reply
    assert len(chat_calls) == 1
    assert audit["review_status"] == "needs_review"
    assert captured["status"] == "needs_review"



def test_language_guard_retries_wrong_language_then_fails_closed(monkeypatch):
    _disable_memory(monkeypatch)
    monkeypatch.setattr(chat_research.web_sources, "discover_sources", lambda *_args, **_kwargs: [])
    french = (
        "Quand deux sources fiables contredisent une affirmation technique, il est préférable de "
        "identifier les sources et noter leurs positions respectives. Examiner les preuves fournies "
        "par chaque source pour comprendre leurs arguments et leurs méthodologies. Si aucune source "
        "supplémentaire n'est disponible, mentionner la contradiction et suggérer une recherche."
    )
    answers = iter([french, french])
    prompts = []

    def fake_chat(message, history=None, system_prompt=None, task="simple_chat"):
        prompts.append(json.loads(message) if message.startswith("{") else message)
        return next(answers)

    monkeypatch.setattr(chat_research.ollama, "chat", fake_chat)
    reply = chat_research.answer(
        "Two credible sources disagree about a technical claim. How should the assistant respond?"
    )

    assert len(prompts) == 2
    assert prompts[1]["instruction"].startswith("Rewrite the answer entirely in English")
    assert "couldn't reliably produce an answer in English" in reply
    assert "Verification note:" not in reply


def test_direct_chat_prompt_treats_user_correction_as_unverified(monkeypatch):
    captured = {}

    def fake_chat(message, history=None, system_prompt=None, task="simple_chat"):
        captured["system_prompt"] = system_prompt
        return "The correction is unverified feedback."

    monkeypatch.setattr(chat_research.ollama, "chat", fake_chat)
    reply = chat_research.answer(
        "A user says my previous answer was wrong and suggests a correction. "
        "Should it become trusted knowledge automatically?"
    )

    assert "unverified feedback" in reply
    assert "unverified feedback" in captured["system_prompt"]
    assert "independent supporting evidence" in captured["system_prompt"]



@pytest.mark.parametrize(
    "prompt",
    [
        "آیا این ادعا را می‌توان فقط بر اساس یک متن بازیابی‌شده تأییدشده دانست؟",
        "دو منبع معتبر درباره یک موضوع فنی نتایج متناقض دارند؛ چگونه پاسخ بدهی؟",
        "کاربر می‌گوید پاسخ قبلی اشتباه است و یک اصلاح پیشنهاد می‌کند؛ آیا آن را به حافظه دانشی معتبر تبدیل می‌کنی؟",
        "پرسش فارسی است اما بهترین منبع موجود انگلیسی است؛ چه باید کرد؟",
        "Can a retrieved passage alone justify marking a claim as verified?",
        "Two credible sources disagree about a technical claim. How should the assistant respond?",
        "A user supplies a correction. Should it become trusted knowledge automatically?",
        "The user asks in English, but the strongest source is written in Persian.",
    ],
)
def test_answer_quality_cases_use_research_policy(prompt):
    assert chat_research.should_use_research(prompt), prompt
