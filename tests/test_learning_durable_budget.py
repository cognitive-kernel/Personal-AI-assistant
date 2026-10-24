import pytest

from app import db, learning
from app.learning_durable_budget import (
    LearningBudgetError,
    get_job_budget,
    reserve_job_budget,
)


@pytest.fixture
def learning_store(tmp_path, monkeypatch):
    monkeypatch.setattr(db.defaults, "settings_db_path", str(tmp_path / "settings.db"))
    monkeypatch.setattr(db.defaults, "learning_db_path", str(tmp_path / "learning.db"))
    db.init_databases()
    return tmp_path


def test_job_budget_is_shared_across_generation_and_review_stages(learning_store):
    job = learning.create_job("Budget test")
    job_id = job["id"]

    first = reserve_job_budget(job_id, "initial_generation", calls=1, predict_tokens=1200)
    repair = reserve_job_budget(job_id, "quality_repair", calls=1, predict_tokens=1400)
    web = reserve_job_budget(job_id, "candidate_review_research_web", calls=1, predict_tokens=1800)
    peer = reserve_job_budget(job_id, "candidate_review_peer_ai", calls=3, predict_tokens=4500)
    sandbox = reserve_job_budget(job_id, "sandbox_execution_0123456789abcdef", calls=1, predict_tokens=0)

    assert all(item["reserved"] for item in (first, repair, web, peer, sandbox))
    snapshot = get_job_budget(job_id)
    assert snapshot["calls_used"] == 7
    assert snapshot["predict_tokens_reserved"] == 8900
    assert snapshot["max_calls"] == 7
    assert snapshot["max_predict_tokens"] == 9000
    assert snapshot["billing_cost_known"] is False


def test_duplicate_stage_reservation_is_idempotent_and_cannot_change_cost(learning_store):
    job_id = learning.create_job("Budget idempotency")["id"]
    first = reserve_job_budget(job_id, "initial_generation", calls=1, predict_tokens=1200)
    repeated = reserve_job_budget(job_id, "initial_generation", calls=1, predict_tokens=1200)

    assert first["reserved"] is True
    assert repeated["reserved"] is True
    assert repeated["already_reserved"] is True
    assert get_job_budget(job_id)["calls_used"] == 1
    assert get_job_budget(job_id)["predict_tokens_reserved"] == 1200

    with pytest.raises(LearningBudgetError, match="different reservations"):
        reserve_job_budget(job_id, "initial_generation", calls=2, predict_tokens=1500)


def test_job_budget_fails_closed_when_call_or_token_ceiling_would_be_exceeded(learning_store):
    job_id = learning.create_job("Budget ceiling")["id"]
    reserve_job_budget(job_id, "generation", calls=1, predict_tokens=1200)
    denied = reserve_job_budget(job_id, "peer_review", calls=3, predict_tokens=8500)

    assert denied["reserved"] is False
    assert denied["reason"] == "budget_exhausted"
    assert get_job_budget(job_id)["calls_used"] == 1
    assert get_job_budget(job_id)["predict_tokens_reserved"] == 1200


def test_job_budget_ceilings_are_immutable(learning_store):
    job_id = learning.create_job("Budget immutable")["id"]
    reserve_job_budget(job_id, "generation", calls=1, predict_tokens=1200)
    with pytest.raises(LearningBudgetError, match="ceilings are immutable"):
        reserve_job_budget(
            job_id, "another-stage", calls=1, predict_tokens=100,
            max_calls=8, max_predict_tokens=10000,
        )


@pytest.mark.parametrize(
    "kwargs",
    [
        {"calls": True, "predict_tokens": 1},
        {"calls": -1, "predict_tokens": 1},
        {"calls": 1, "predict_tokens": -1},
        {"calls": 1, "predict_tokens": 0},
    ],
)
def test_job_budget_rejects_invalid_reservations(learning_store, kwargs):
    job_id = learning.create_job("Budget invalid")["id"]
    with pytest.raises(LearningBudgetError):
        reserve_job_budget(job_id, "bad-stage", **kwargs)
