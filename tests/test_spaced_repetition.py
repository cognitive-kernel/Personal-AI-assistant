from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app import cloud_resources, db, spaced_repetition
from app.main import app
from app.db import learning_connection


def _setup(tmp_path, monkeypatch):
    monkeypatch.setattr(db.defaults, "settings_db_path", str(tmp_path / "settings.db"))
    monkeypatch.setattr(db.defaults, "learning_db_path", str(tmp_path / "learning.db"))
    monkeypatch.setattr(db.defaults, "cloud_resources_db_path", str(tmp_path / "cloud_resources.db"))
    db.init_databases()
    with learning_connection() as connection:
        path_id = connection.execute(
            "INSERT INTO learning_paths(curriculum_slug, title) VALUES (?, ?)",
            ("spaced-repetition-test", "Spaced repetition test"),
        ).lastrowid
        topic_id = connection.execute(
            "INSERT INTO learning_path_topics(path_id, topic, position) VALUES (?, ?, 1)",
            (path_id, "Recall topic"),
        ).lastrowid
    return int(path_id), int(topic_id)


def test_spaced_repetition_intervals_follow_deterministic_rating_policy(tmp_path, monkeypatch):
    path_id, topic_id = _setup(tmp_path, monkeypatch)
    now = datetime(2026, 10, 16, 12, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(spaced_repetition, "_now", lambda: now)

    first = spaced_repetition.record_review(path_id, topic_id, 4)
    assert first["repetitions"] == 1
    assert first["interval_days"] == 1
    assert first["due_at"] == "2026-10-17T12:00:00Z"

    second = spaced_repetition.record_review(path_id, topic_id, 4)
    assert second["repetitions"] == 2
    assert second["interval_days"] == 6

    third = spaced_repetition.record_review(path_id, topic_id, 5)
    assert third["repetitions"] == 3
    assert third["interval_days"] == 15
    assert third["ease_factor"] == 2.6

    lapse = spaced_repetition.record_review(path_id, topic_id, 2)
    assert lapse["repetitions"] == 0
    assert lapse["interval_days"] == 1
    assert lapse["ease_factor"] == 2.4

    history = spaced_repetition.get_topic_review_history(path_id, topic_id)
    assert history["count"] == 4
    assert history["schedule"]["last_rating"] == 2


def test_due_reviews_only_include_schedules_whose_due_time_has_arrived(tmp_path, monkeypatch):
    path_id, topic_id = _setup(tmp_path, monkeypatch)
    base = datetime(2026, 10, 16, 12, 0, tzinfo=timezone.utc)
    current = {"value": base}
    monkeypatch.setattr(spaced_repetition, "_now", lambda: current["value"])

    spaced_repetition.record_review(path_id, topic_id, 4)
    assert spaced_repetition.list_due_reviews() == []

    current["value"] = base + timedelta(days=1, seconds=1)
    due = spaced_repetition.list_due_reviews()
    assert len(due) == 1
    assert due[0]["topic_id"] == topic_id
    assert due[0]["path_title"] == "Spaced repetition test"


@pytest.mark.parametrize("rating", [-1, 6, True, 1.5, "4"])
def test_spaced_repetition_rejects_invalid_ratings(tmp_path, monkeypatch, rating):
    path_id, topic_id = _setup(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="rating must be an integer"):
        spaced_repetition.record_review(path_id, topic_id, rating)


def test_spaced_repetition_requires_topic_to_belong_to_path(tmp_path, monkeypatch):
    path_id, topic_id = _setup(tmp_path, monkeypatch)
    with pytest.raises(KeyError):
        spaced_repetition.record_review(path_id + 1, topic_id, 4)



def test_learning_ui_exposes_spaced_repetition_controls():
    from pathlib import Path

    page = (Path(__file__).parents[1] / "app" / "static" / "learning.js").read_text(
        encoding="utf-8"
    )
    index = (Path(__file__).parents[1] / "app" / "static" / "index.html").read_text(
        encoding="utf-8"
    )
    assert 'id="refresh-due-reviews"' in index
    assert "نمایش مرورهای سررسیدشده" in index
    assert "ثبت مرور و زمان‌بندی بعدی" in page
    assert '"/api/learning/reviews/due?limit=100"' in page
    assert '"/api/learning/paths/" + path.id + "/topics/" + topic.id + "/reviews"' in page

def test_learning_task_audit_history_formats_each_event_kind():
    from pathlib import Path

    page = (Path(__file__).parents[1] / "app" / "static" / "learning.js").read_text(
        encoding="utf-8"
    )
    assert "function formatTaskAuditEventChange(event)" in page
    assert 'case "status_changed"' in page
    assert 'case "priority_changed"' in page
    assert 'case "scheduled"' in page
    assert 'case "created"' in page
    assert '"اولویت: " + (event.old_priority ?? "—")' in page
    assert '"زمان‌بندی: " + (event.old_scheduled_at || "—")' in page
    assert "const change = formatTaskAuditEventChange(event);" in page
    assert 'olderEventsButton.textContent = "بارگذاری رویدادهای قدیمی‌تر"' in page
    assert '"/events?limit=100" + cursor' in page
    assert '"&before_id=" + encodeURIComponent(beforeId)' in page
    assert "response.has_more ? response.next_before_id : null" in page
    assert "eventsList.prepend(...items)" in page




def test_spaced_repetition_api_is_loopback_only_and_persists_reviews(tmp_path, monkeypatch):
    path_id, topic_id = _setup(tmp_path, monkeypatch)
    monkeypatch.setattr(
        cloud_resources, "ensure_daily_discovery_scheduled", lambda: {"status": "disabled"}
    )
    with TestClient(app, client=("127.0.0.1", 12345)) as client:
        saved = client.post(
            f"/api/learning/paths/{path_id}/topics/{topic_id}/reviews",
            json={"rating": 5},
        )
        assert saved.status_code == 200
        assert saved.json()["interval_days"] == 1

        history = client.get(
            f"/api/learning/paths/{path_id}/topics/{topic_id}/reviews"
        )
        assert history.status_code == 200
        assert history.json()["count"] == 1

        due = client.get("/api/learning/reviews/due")
        assert due.status_code == 200
        assert due.json()["count"] == 0

    remote = TestClient(app, client=("192.0.2.5", 12345))
    denied = remote.get("/api/learning/reviews/due")
    assert denied.status_code == 403
