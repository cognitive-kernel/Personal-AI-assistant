import json

import pytest

from app import db, learning, ollama, learning_durable_budget, claim_review, learning_evidence_gate


@pytest.fixture
def learning_store(tmp_path, monkeypatch):
    monkeypatch.setattr(db.defaults, "settings_db_path", str(tmp_path / "settings.db"))
    monkeypatch.setattr(db.defaults, "learning_db_path", str(tmp_path / "learning.db"))
    db.init_databases()
    monkeypatch.setattr(
        learning,
        "detect_hardware",
        lambda: {"logical_cpu_count": 4, "total_ram_gb": 16, "available_ram_gb": 12},
    )


def _valid_payload(lesson="Python decorators wrap callables and can add reusable behavior."):
    return {
        "lesson": lesson,
        "learning_outcomes": [
            "Describe the decorator pattern",
            "Explain how a wrapper calls a function",
            "Identify a reusable decorator use case",
        ],
        "questions": [
            "What does a decorator wrap?",
            "What does a wrapper usually do?",
            "When is a decorator useful?",
        ],
        "answer_key": [
            "A callable.",
            "It calls the original function and returns its result.",
            "When behavior should be reused across functions.",
        ],
    }


def test_ollama_usage_summary_accepts_only_nonnegative_integer_counters():
    summary = learning._ollama_usage_summary({
        "prompt_eval_count": 123,
        "eval_count": 45,
        "total_duration": 9000,
        "eval_duration": -1,
        "load_duration": True,
        "billing_cost": 123.45,
        "message": {"content": "ignored"},
    })

    assert summary == {
        "prompt_eval_count": 123,
        "eval_count": 45,
        "total_duration": 9000,
    }
    assert learning._ollama_usage_summary(None) == {}


def test_malformed_initial_response_enters_bounded_repair_cycle(learning_store, monkeypatch):
    calls = []
    responses = iter([
        {"message": {"content": "not valid json"}, "prompt_eval_count": 800, "eval_count": 120, "total_duration": 900000},
        {"message": {"content": json.dumps(_valid_payload(), ensure_ascii=False)}, "prompt_eval_count": 1100, "eval_count": 900, "eval_duration": 500000},
    ])
    monkeypatch.setattr(
        learning, "_request",
        lambda _path, payload, **_kwargs: calls.append(payload) or next(responses),
    )
    quality_results = iter([
        {"accepted": False, "score": 0, "reasons": ["malformed initial output"]},
        {"accepted": True, "score": 90, "reasons": []},
    ])
    monkeypatch.setattr(
        learning, "evaluate_learning_value",
        lambda *_args, **_kwargs: next(quality_results),
    )

    job = learning.create_job("Python decorators", source_text="Reference text for the lesson.")
    learning.run_job(job["id"])
    finished = learning.get_job(job["id"])

    assert finished["status"] == "completed"
    assert len(calls) == 2
    quality = finished["quality"]
    assert quality["generation_budget"]["calls_used"] == 2
    assert quality["generation_budget"]["predict_tokens_reserved"] == 2600
    assert quality["generation_budget"]["budget_exhausted"] is True
    assert quality["pipeline_budget"]["calls_used"] == 2
    assert quality["pipeline_budget"]["predict_tokens_reserved"] == 2600
    assert quality["pipeline_budget"]["scope"] == "durable_single_learning_job_across_generation_and_review_channels"
    assert quality["generation_provenance"]["initial_response_malformed"] is True
    assert quality["generation_provenance"]["repair_response_valid"] is True
    assert quality["generation_provenance"]["attempt_count"] == 2
    assert len(quality["generation_provenance"]["repair_prompt_sha256"]) == 64
    assert quality["generation_provenance"]["prompt_registry_version"] == 1
    assert quality["generation_provenance"]["prompt_version"] == "learning-lesson-v1"
    assert quality["generation_provenance"]["repair_prompt_version"] == "learning-lesson-repair-v1"
    assert quality["generation_provenance"]["provider_usage"] == {
        "initial_generation": {"prompt_eval_count": 800, "eval_count": 120, "total_duration": 900000},
        "quality_repair": {"prompt_eval_count": 1100, "eval_count": 900, "eval_duration": 500000},
    }


def test_lower_scoring_repair_does_not_replace_best_initial_quality(learning_store, monkeypatch):
    calls = []
    initial = _valid_payload("A decorator wraps a callable and can add reusable behavior.")
    repair = _valid_payload("Short repair.")
    responses = iter([
        {"message": {"content": json.dumps(initial, ensure_ascii=False)}},
        {"message": {"content": json.dumps(repair, ensure_ascii=False)}},
    ])
    monkeypatch.setattr(
        learning, "_request",
        lambda _path, payload, **_kwargs: calls.append(payload) or next(responses),
    )
    quality_results = iter([
        {"accepted": False, "score": 55, "reasons": ["needs a concrete example"]},
        {"accepted": False, "score": 20, "reasons": ["repair is worse"]},
    ])
    monkeypatch.setattr(
        learning, "evaluate_learning_value",
        lambda *_args, **_kwargs: next(quality_results),
    )

    job = learning.create_job("Python decorators", source_text="Reference text for the lesson.")
    learning.run_job(job["id"])
    finished = learning.get_job(job["id"])

    assert finished["status"] == "rejected"
    assert len(calls) == 2
    assert finished["quality"]["score"] == 55
    assert finished["quality"]["reasons"] == ["needs a concrete example"]
    assert finished["quality"]["generation_budget"]["calls_used"] == 2
    assert finished["quality"]["generation_provenance"]["attempt_count"] == 2
    assert finished["lesson"] is None

def test_accepted_initial_lesson_has_no_repair_prompt_provenance(learning_store, monkeypatch):
    payload = _valid_payload()
    calls = []
    monkeypatch.setattr(
        learning, "_request",
        lambda _path, request, **_kwargs: calls.append(request) or {
            "message": {"content": json.dumps(payload, ensure_ascii=False)}
        },
    )
    monkeypatch.setattr(
        learning, "evaluate_learning_value",
        lambda *_args, **_kwargs: {"accepted": True, "score": 95, "reasons": []},
    )

    job = learning.create_job("Python decorators", source_text="Reference text for the lesson.")
    learning.run_job(job["id"])
    finished = learning.get_job(job["id"])

    assert finished["status"] == "completed"
    assert len(calls) == 1
    provenance = finished["quality"]["generation_provenance"]
    assert provenance["prompt_version"] == "learning-lesson-v1"
    assert provenance["repair_prompt_version"] is None
    assert provenance["repair_prompt_sha256"] is None
    assert provenance["attempt_count"] == 1




def test_learning_generation_uses_cheapest_installed_model_meeting_quality_and_budget(
    learning_store, monkeypatch
):
    db.update_settings({
        "learning_model_cost_profiles": {
            "cheap:latest": {
                "input_cost_microusd_per_1k_tokens": 100,
                "output_cost_microusd_per_1k_tokens": 100,
                "quality_score": 0.85,
            },
            "premium:latest": {
                "input_cost_microusd_per_1k_tokens": 500,
                "output_cost_microusd_per_1k_tokens": 500,
                "quality_score": 0.99,
            },
        },
        "learning_model_minimum_quality_score": 0.8,
        "learning_task_budget_microusd": 100000,
    })
    monkeypatch.setattr(
        ollama, "list_models",
        lambda: [{"name": "cheap:latest"}, {"name": "premium:latest"}],
    )
    calls = []
    monkeypatch.setattr(
        learning, "_request",
        lambda _path, request, **_kwargs: calls.append(request) or {
            "message": {"content": json.dumps(_valid_payload(), ensure_ascii=False)}
        },
    )
    monkeypatch.setattr(
        learning, "evaluate_learning_value",
        lambda *_args, **_kwargs: {"accepted": True, "score": 95, "reasons": []},
    )

    job = learning.create_job("Python decorators", source_text="Reference text for the lesson.")
    learning.run_job(job["id"])

    finished = learning.get_job(job["id"])
    provenance = finished["quality"]["generation_provenance"]
    assert finished["status"] == "completed"
    assert len(calls) == 1
    assert calls[0]["model"] == "cheap:latest"
    assert provenance["model_id"] == "cheap:latest"
    assert provenance["model_selection"]["status"] == "selected"
    assert provenance["model_selection"]["estimated_cost_microusd"] <= 100000
    assert provenance["model_selection"]["budget_scope"] == "lesson_generation_single_repair_and_optional_claim_review"


def test_learning_generation_fails_closed_when_no_model_meets_explicit_budget(
    learning_store, monkeypatch
):
    db.update_settings({
        "learning_model_cost_profiles": {
            "over-budget:latest": {
                "input_cost_microusd_per_1k_tokens": 100000,
                "output_cost_microusd_per_1k_tokens": 100000,
                "quality_score": 0.99,
            },
        },
        "learning_model_minimum_quality_score": 0.8,
        "learning_task_budget_microusd": 1,
    })
    monkeypatch.setattr(
        ollama, "list_models", lambda: [{"name": "over-budget:latest"}]
    )
    calls = []
    monkeypatch.setattr(
        learning, "_request",
        lambda *_args, **_kwargs: calls.append(True) or {},
    )

    job = learning.create_job("Python decorators", source_text="Reference text for the lesson.")
    with pytest.raises(learning.LearningError, match="No installed learning model"):
        learning.run_job(job["id"])

    finished = learning.get_job(job["id"])
    assert finished["status"] == "failed"
    assert calls == []
    assert learning_durable_budget.get_job_budget(job["id"])["calls_used"] == 0


def test_learning_generation_does_not_bypass_quality_threshold_when_model_discovery_fails(
    learning_store, monkeypatch
):
    db.update_settings({
        "learning_model_cost_profiles": {
            "profiled:latest": {
                "input_cost_microusd_per_1k_tokens": 100,
                "output_cost_microusd_per_1k_tokens": 100,
                "quality_score": 0.95,
            },
        },
        "learning_model_minimum_quality_score": 0.9,
        "learning_task_budget_microusd": None,
    })
    monkeypatch.setattr(
        ollama, "list_models",
        lambda: (_ for _ in ()).throw(RuntimeError("Ollama unavailable")),
    )
    calls = []
    monkeypatch.setattr(
        learning, "_request",
        lambda *_args, **_kwargs: calls.append(True) or {},
    )

    job = learning.create_job("Python decorators", source_text="Reference text for the lesson.")
    with pytest.raises(learning.LearningError, match="Cannot enforce learning model"):
        learning.run_job(job["id"])

    assert learning.get_job(job["id"])["status"] == "failed"
    assert calls == []
    assert learning_durable_budget.get_job_budget(job["id"])["calls_used"] == 0


def test_learning_generation_preserves_configured_model_when_cost_profiles_are_absent(
    learning_store, monkeypatch
):
    calls = []
    monkeypatch.setattr(
        ollama, "list_models",
        lambda: (_ for _ in ()).throw(AssertionError("model discovery must be skipped")),
    )
    monkeypatch.setattr(
        learning, "_request",
        lambda _path, request, **_kwargs: calls.append(request) or {
            "message": {"content": json.dumps(_valid_payload(), ensure_ascii=False)}
        },
    )
    monkeypatch.setattr(
        learning, "evaluate_learning_value",
        lambda *_args, **_kwargs: {"accepted": True, "score": 95, "reasons": []},
    )

    job = learning.create_job("Python decorators", source_text="Reference text for the lesson.")
    learning.run_job(job["id"])

    finished = learning.get_job(job["id"])
    provenance = finished["quality"]["generation_provenance"]
    assert finished["status"] == "completed"
    assert calls[0]["model"] == db.get_settings()["learning_ollama_model"]
    assert provenance["model_selection"]["status"] == "configured_fallback"
    assert provenance["model_selection"]["estimated_cost_microusd"] is None



def test_web_claim_review_uses_selected_model_and_is_included_in_task_cost_estimate(
    learning_store, monkeypatch
):
    url = "https://docs.python.org/3/library/json.html"
    source_text = (
        "Source title: Python JSON docs\n"
        f"Source URL: {url}\n"
        "Content:\n"
        "Invalid JSON input may raise a decoding error. Applications should handle malformed input carefully. "
        "The parser reports the location of the invalid input so callers can correct the source data."
    )
    db.update_settings({
        "learning_model_cost_profiles": {
            "cheap:latest": {
                "input_cost_microusd_per_1k_tokens": 100,
                "output_cost_microusd_per_1k_tokens": 100,
                "quality_score": 0.9,
            },
            "premium:latest": {
                "input_cost_microusd_per_1k_tokens": 500,
                "output_cost_microusd_per_1k_tokens": 500,
                "quality_score": 0.99,
            },
        },
        "learning_model_minimum_quality_score": 0.8,
        "learning_task_budget_microusd": 100000,
    })
    monkeypatch.setattr(
        ollama, "list_models",
        lambda: [{"name": "cheap:latest"}, {"name": "premium:latest"}],
    )
    generation_calls = []
    monkeypatch.setattr(
        learning, "_request",
        lambda _path, request, **_kwargs: generation_calls.append(request) or {
            "message": {"content": json.dumps(_valid_payload(), ensure_ascii=False)}
        },
    )
    monkeypatch.setattr(
        learning, "evaluate_learning_value",
        lambda *_args, **_kwargs: {"accepted": True, "score": 95, "reasons": []},
    )
    review_calls = []

    def fake_review(question, answer, sources, *, model_override=None):
        review_calls.append({
            "question": question,
            "answer": answer,
            "sources": sources,
            "model_override": model_override,
        })
        return {
            "passed": True,
            "overall": "supported",
            "claims": [],
            "source_conflicts": [],
            "model_id": model_override,
            "prompt_version": "claim-review-test-v1",
            "prompt_sha256": "a" * 64,
            "model_digest": None,
            "model_digest_status": "unavailable_from_current_ollama_path",
            "provider_usage": {"prompt_eval_count": 800, "eval_count": 120},
            "reviewer": "local_ollama_model_assisted_not_independent",
        }

    monkeypatch.setattr(claim_review, "review_answer_claims", fake_review)
    monkeypatch.setattr(
        learning_evidence_gate, "evaluate_lesson_evidence",
        lambda *_args, **_kwargs: {
            "version": learning_evidence_gate.GATE_VERSION,
            "status": "accepted",
            "accepted": True,
            "independent_scientific_verification": False,
            "failed_gates": [],
        },
    )

    job = learning.create_job("Python JSON parsing", source_text=source_text)
    with learning.learning_connection() as conn:
        conn.execute(
            "UPDATE learning_jobs SET source_kind='web_retrieved' WHERE id=?",
            (job["id"],),
        )
    learning.run_job(job["id"])

    finished = learning.get_job(job["id"])
    selection = finished["quality"]["generation_provenance"]["model_selection"]
    assert finished["status"] == "completed"
    assert generation_calls[0]["model"] == "cheap:latest"
    assert len(review_calls) == 1
    assert review_calls[0]["model_override"] == "cheap:latest"
    assert selection["claim_review_included"] is True
    assert selection["claim_review_prompt_tokens"] > 0
    assert selection["estimated_completion_tokens"] == 3600
    assert selection["budget_scope"] == "lesson_generation_single_repair_and_optional_claim_review"
    generation_provenance = finished["quality"]["generation_provenance"]
    assert generation_provenance["provider_usage"]["claim_review"] == {
        "prompt_eval_count": 800, "eval_count": 120
    }
    assert generation_provenance["claim_review"]["prompt_sha256"] == "a" * 64
    assert generation_provenance["claim_review"]["model_id"] == "cheap:latest"
    durable = learning_durable_budget.get_job_budget(job["id"])
    assert durable["calls_used"] == 2
    assert durable["predict_tokens_reserved"] == 2200



def test_web_claim_review_does_not_repeat_an_already_reserved_review_stage(
    learning_store, monkeypatch
):
    url = "https://docs.python.org/3/library/json.html"
    source_text = (
        "Source title: Python JSON docs\n"
        f"Source URL: {url}\n"
        "Content:\n"
        "Invalid JSON input may raise a decoding error. Applications should handle malformed input carefully. "
        "The parser reports the location of the invalid input so callers can correct the source data."
    )
    db.update_settings({
        "learning_model_cost_profiles": {
            "cheap:latest": {
                "input_cost_microusd_per_1k_tokens": 100,
                "output_cost_microusd_per_1k_tokens": 100,
                "quality_score": 0.9,
            },
        },
        "learning_model_minimum_quality_score": 0.8,
        "learning_task_budget_microusd": 100000,
    })
    monkeypatch.setattr(
        ollama, "list_models", lambda: [{"name": "cheap:latest"}]
    )
    monkeypatch.setattr(
        learning, "_request",
        lambda *_args, **_kwargs: {
            "message": {"content": json.dumps(_valid_payload(), ensure_ascii=False)}
        },
    )
    monkeypatch.setattr(
        learning, "evaluate_learning_value",
        lambda *_args, **_kwargs: {"accepted": True, "score": 95, "reasons": []},
    )
    review_calls = []
    monkeypatch.setattr(
        claim_review, "review_answer_claims",
        lambda *_args, **_kwargs: review_calls.append(True),
    )
    monkeypatch.setattr(
        learning_evidence_gate, "evaluate_lesson_evidence",
        lambda *_args, **_kwargs: {
            "version": learning_evidence_gate.GATE_VERSION,
            "status": "not_accepted",
            "accepted": False,
            "independent_scientific_verification": False,
            "failed_gates": ["claim_review_budget"],
        },
    )

    job = learning.create_job("Python JSON parsing", source_text=source_text)
    with learning.learning_connection() as conn:
        conn.execute(
            "UPDATE learning_jobs SET source_kind='web_retrieved' WHERE id=?",
            (job["id"],),
        )
    reserved = learning_durable_budget.reserve_job_budget(
        job["id"], "claim_review", calls=1, predict_tokens=1000
    )
    assert reserved["reserved"] is True

    learning.run_job(job["id"])

    finished = learning.get_job(job["id"])
    assert review_calls == []
    assert finished["quality"]["claim_review_status"] == "needs_review"
    assert finished["quality"]["claim_review"]["error"] == (
        "claim_review_budget_exhausted_or_already_reserved"
    )
    durable = learning_durable_budget.get_job_budget(job["id"])
    assert durable["calls_used"] == 2
    assert durable["predict_tokens_reserved"] == 2200
