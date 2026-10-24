import json

import pytest

from app import db, learning


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


def test_malformed_initial_response_enters_bounded_repair_cycle(learning_store, monkeypatch):
    calls = []
    responses = iter([
        {"message": {"content": "not valid json"}},
        {"message": {"content": json.dumps(_valid_payload(), ensure_ascii=False)}},
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
    assert quality["generation_provenance"]["initial_response_malformed"] is True
    assert quality["generation_provenance"]["repair_response_valid"] is True
    assert quality["generation_provenance"]["attempt_count"] == 2
    assert len(quality["generation_provenance"]["repair_prompt_sha256"]) == 64


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
