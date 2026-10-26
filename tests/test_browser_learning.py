import json

import pytest

from app import browser_learning, db


def _init(tmp_path, monkeypatch):
    monkeypatch.setattr(db.defaults, "settings_db_path", str(tmp_path / "settings.db"))
    monkeypatch.setattr(db.defaults, "learning_db_path", str(tmp_path / "learning.db"))
    db.init_databases()


@pytest.mark.parametrize("url", [
    "http://example.com:9222",
    "https://127.0.0.1:9222",
    "http://127.0.0.1:9222/path",
    "http://user:pass@127.0.0.1:9222",
    "http://127.0.0.1:9222?token=secret",
    "http://127.0.0.1:99999",
])
def test_cdp_endpoint_rejects_non_loopback_or_ambiguous_urls(url):
    with pytest.raises(ValueError):
        browser_learning._valid_cdp_url(url)


def test_cdp_endpoint_accepts_loopback_only():
    assert browser_learning._valid_cdp_url("http://127.0.0.1:9222/") == "http://127.0.0.1:9222"
    assert browser_learning._valid_cdp_url("http://localhost:9222") == "http://localhost:9222"
    assert browser_learning._valid_cdp_url("http://[::1]:9222") == "http://[::1]:9222"


def test_parse_outline_json_limits_deduplicates_and_preserves_title():
    title, topics = browser_learning._parse_outline(
        '{"title":"SQL Server course","topics":["Intro","Indexes","Intro","Query plans"]}',
        2,
    )
    assert title == "SQL Server course"
    assert topics == ["Intro", "Indexes"]


def test_parse_outline_accepts_numbered_markdown_fallback():
    title, topics = browser_learning._parse_outline(
        "سرفصل‌های دوره\n1. نصب و معماری\n2) پایگاه داده\n- امنیت",
        3,
    )
    assert title
    assert topics == ["سرفصل‌های دوره", "نصب و معماری", "پایگاه داده"]


def test_browser_learning_course_persists_without_marking_content_verified(tmp_path, monkeypatch):
    _init(tmp_path, monkeypatch)
    course = browser_learning.create_course("SQL Server 2002", "http://127.0.0.1:9222", 12)
    assert course["status"] == "queued"
    assert course["topic"] == "SQL Server 2002"
    with db.learning_connection() as conn:
        conn.execute(
            """UPDATE browser_learning_courses SET title=?, outline_json=?, status='running' WHERE id=?""",
            ("SQL Server", json.dumps({"topics": ["Indexes"]}), course["id"]),
        )
        conn.execute(
            """INSERT INTO browser_learning_lessons(course_id, position, topic, status, content,
               source_url, prompt_sha256, content_sha256)
               VALUES (?, 1, ?, 'completed', ?, ?, ?, ?)""",
            (course["id"], "Indexes", "Lesson body", "https://chatgpt.com/", "a" * 64, "b" * 64),
        )
    result = browser_learning.get_course(course["id"])
    assert "cdp_url" not in result
    assert result["lessons"][0]["content"] == "Lesson body"
    assert result["lessons"][0]["content_sha256"] == "b" * 64
    assert result["lessons"][0]["status"] == "completed"


def test_only_one_browser_learning_course_can_be_active(tmp_path, monkeypatch):
    _init(tmp_path, monkeypatch)
    browser_learning.create_course("First course", "http://127.0.0.1:9222", 5)
    with pytest.raises(browser_learning.BrowserLearningError):
        browser_learning.create_course("Second course", "http://127.0.0.1:9222", 5)


def test_retry_resets_incomplete_lessons_but_keeps_completed_content(tmp_path, monkeypatch):
    _init(tmp_path, monkeypatch)
    course = browser_learning.create_course("Course", "http://127.0.0.1:9222", 2)
    with db.learning_connection() as conn:
        conn.execute(
            """UPDATE browser_learning_courses SET status='failed', title='Saved title',
               outline_json=? WHERE id=?""",
            (json.dumps({"topics": ["One", "Two"]}), course["id"]),
        )
        conn.execute(
            """INSERT INTO browser_learning_lessons(course_id, position, topic, status, content)
               VALUES (?, 1, 'One', 'completed', 'retained')""",
            (course["id"],),
        )
        conn.execute(
            """INSERT INTO browser_learning_lessons(course_id, position, topic, status, error)
               VALUES (?, 2, 'Two', 'failed', 'temporary error')""",
            (course["id"],),
        )
    result = browser_learning.retry_course(course["id"])
    assert result["status"] == "queued"
    with db.learning_connection() as conn:
        lessons = conn.execute(
            "SELECT position, status, content, error FROM browser_learning_lessons WHERE course_id=? ORDER BY position",
            (course["id"],),
        ).fetchall()
    assert lessons[0]["status"] == "completed" and lessons[0]["content"] == "retained"
    assert lessons[1]["status"] == "queued" and lessons[1]["error"] is None
