from __future__ import annotations

import pytest

from app.ai_learning import service
from app.ai_learning.db import connection, initialize_store


@pytest.fixture
def isolated_store(tmp_path, monkeypatch):
    monkeypatch.setenv("AI_LEARNING_DB_PATH", str(tmp_path / "ai_learning.db"))
    initialize_store()


def test_feedback_is_bound_to_trace_and_does_not_store_raw_query(isolated_store):
    trace_id = service.record_query_trace("اطلاعات محرمانهٔ کاربر", [1, 2])
    feedback = service.submit_query_feedback(trace_id, 1, "پاسخ مفید بود")
    assert feedback["query_trace_id"] == trace_id
    assert feedback["rating"] == 1
    assert feedback["comment"] == "پاسخ مفید بود"

    with connection() as conn:
        trace = conn.execute("SELECT * FROM query_traces WHERE id=?", (trace_id,)).fetchone()
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(query_traces)")}
        assert "query" not in columns
        assert "اطلاعات محرمانهٔ کاربر" not in str(dict(trace))

    summary = service.query_feedback_summary()
    assert summary == {
        "total": 1,
        "helpful": 1,
        "not_helpful": 0,
        "helpful_rate": 1.0,
    }


def test_feedback_can_be_changed_without_duplicate_rows(isolated_store):
    trace_id = service.record_query_trace("جست‌وجوی دانش", [])
    first = service.submit_query_feedback(trace_id, 1, "اول")
    second = service.submit_query_feedback(trace_id, -1, "پس از بررسی، ناکافی بود")
    assert first["id"] == second["id"]
    assert second["rating"] == -1
    assert service.query_feedback_summary() == {
        "total": 1,
        "helpful": 0,
        "not_helpful": 1,
        "helpful_rate": 0.0,
    }
    with connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM query_feedback").fetchone()[0] == 1


@pytest.mark.parametrize(
    ("trace_id", "rating", "comment"),
    [(0, 1, ""), (1, 0, ""), (1, True, ""), (1, 1.0, ""), (1, -1.0, ""), (1, "1", ""), (1, -1, "x" * 1001)],
)
def test_feedback_rejects_invalid_values(isolated_store, trace_id, rating, comment):
    with pytest.raises(ValueError):
        service.submit_query_feedback(trace_id, rating, comment)


def test_feedback_rejects_unknown_trace(isolated_store):
    with pytest.raises(service.NotFoundError):
        service.submit_query_feedback(999, 1, "")
