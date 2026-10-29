import json
import time

import pytest

from app import browser_learning, db


def _init(tmp_path, monkeypatch):
    monkeypatch.setattr(db.defaults, "settings_db_path", str(tmp_path / "settings.db"))
    monkeypatch.setattr(db.defaults, "learning_db_path", str(tmp_path / "learning.db"))
    db.init_databases()
    monkeypatch.setattr(browser_learning, "_ACTIVE_COURSE_ID", None)
    browser_learning._WORKER_LEASE_LOST.clear()
    browser_learning._WORKER_LEASE_STOP.set()
    browser_learning._WORKER_LEASE_THREAD = None


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


def test_chatgpt_page_host_is_exact_and_conversation_url_is_sanitized():
    assert browser_learning._is_chatgpt_url("https://chatgpt.com/c/abc")
    assert browser_learning._is_chatgpt_url("https://chat.openai.com/c/abc")
    assert not browser_learning._is_chatgpt_url("https://chatgpt.com.attacker.invalid/")
    assert not browser_learning._is_chatgpt_url("http://chatgpt.com/c/abc")
    assert browser_learning._chatgpt_source_url("https://chatgpt.com/c/abc?secret=value#fragment") == "https://chatgpt.com/c/abc"


def test_parse_outline_json_limits_deduplicates_and_preserves_title():
    title, topics = browser_learning._parse_outline(
        '{"title":"SQL Server course","topics":["Intro","Indexes","Intro","Query plans"]}',
        2,
    )
    assert title == "SQL Server course"
    assert topics == ["Intro", "Indexes"]


def test_parse_outline_extracts_json_from_surrounding_text():
    title, topics = browser_learning._parse_outline(
        'Here is the plan: {"title":"SQL Server","topics":["Indexes","Transactions"]} End.',
        2,
    )
    assert title == "SQL Server"
    assert topics == ["Indexes", "Transactions"]


def test_parse_outline_accepts_numbered_markdown_fallback():
    title, topics = browser_learning._parse_outline(
        "سرفصل‌های دوره\n1. نصب و معماری\n2) پایگاه داده\n- امنیت",
        3,
    )
    assert title
    assert topics == ["نصب و معماری", "پایگاه داده", "امنیت"]


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
    assert result["lessons"][0]["trust_status"] == "unverified"


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


def test_run_course_uses_browser_chatgpt_flow_and_persists_each_lesson(tmp_path, monkeypatch):
    import sys
    import types

    _init(tmp_path, monkeypatch)

    class FakeLocator:
        def __init__(self, page, kind):
            self.page = page
            self.kind = kind

        @property
        def last(self):
            return self

        def count(self):
            if self.kind == "editor":
                return 1
            if self.kind == "assistant":
                return len(self.page.responses)
            if self.kind == "send":
                return 1
            return 0

        def is_visible(self):
            return True

        def is_enabled(self):
            return True

        def fill(self, value):
            self.page.prompt = value

        def click(self):
            if "JSON معتبر" in self.page.prompt:
                self.page.responses.append(
                    '{"title":"Demo course","topics":["First lesson","Second lesson"]}'
                )
            else:
                self.page.responses.append("درس ذخیره‌شده برای: " + self.page.prompt[-100:])

        def inner_text(self, timeout=None):
            if not self.page.responses:
                raise RuntimeError("No assistant response")
            return self.page.responses[-1]

        def press(self, key):
            self.click()

    class FakePage:
        url = "https://chatgpt.com/c/fake-conversation"

        def __init__(self):
            self.prompt = ""
            self.responses = []

        def goto(self, url, wait_until=None, timeout=None):
            self.url = url

        def locator(self, selector):
            if selector in {"#prompt-textarea", "[data-testid='prompt-textarea']", "div[contenteditable='true'][role='textbox']", "textarea"}:
                return FakeLocator(self, "editor")
            if selector in {"button[data-testid='send-button']", "button[aria-label*='Send']", "button[aria-label*='ارسال']"}:
                return FakeLocator(self, "send")
            if selector == "[data-message-author-role='assistant']":
                return FakeLocator(self, "assistant")
            return FakeLocator(self, "other")

    page = FakePage()

    class FakeContext:
        pages = []

        def new_page(self):
            return page

    class FakeBrowser:
        contexts = [FakeContext()]

    class FakeChromium:
        def connect_over_cdp(self, url, timeout):
            assert url == "http://127.0.0.1:9222"
            return FakeBrowser()

    class FakePlaywright:
        chromium = FakeChromium()

    class FakePlaywrightManager:
        def __enter__(self):
            return FakePlaywright()

        def __exit__(self, exc_type, exc, tb):
            return False

    playwright_package = types.ModuleType("playwright")
    playwright_sync = types.ModuleType("playwright.sync_api")
    playwright_sync.sync_playwright = lambda: FakePlaywrightManager()
    playwright_package.sync_api = playwright_sync
    monkeypatch.setitem(sys.modules, "playwright", playwright_package)
    monkeypatch.setitem(sys.modules, "playwright.sync_api", playwright_sync)
    monkeypatch.setattr(browser_learning.time, "sleep", lambda _seconds: None)

    course = browser_learning.create_course("Demo topic", "http://127.0.0.1:9222", 2)
    assert browser_learning._acquire_course_worker_lease() is True
    browser_learning.run_course(course["id"])
    result = browser_learning.get_course(course["id"])

    assert result["status"] == "completed"
    assert result["title"] == "Demo course"
    assert len(result["outline_sha256"]) == 64
    assert [lesson["topic"] for lesson in result["lessons"]] == ["First lesson", "Second lesson"]
    assert all(lesson["status"] == "completed" for lesson in result["lessons"])
    assert all(lesson["trust_status"] == "unverified" for lesson in result["lessons"])
    assert all(len(lesson["prompt_sha256"]) == 64 for lesson in result["lessons"])
    assert all(len(lesson["content_sha256"]) == 64 for lesson in result["lessons"])


def test_startup_recovery_queues_interrupted_work_and_preserves_completed_lessons(tmp_path, monkeypatch):
    _init(tmp_path, monkeypatch)
    monkeypatch.setattr(browser_learning, "_ACTIVE_COURSE_ID", None)
    course = browser_learning.create_course("Recover a course", "http://127.0.0.1:9222", 2)
    with db.learning_connection() as conn:
        conn.execute(
            """UPDATE browser_learning_courses SET status='running', title='Saved course',
               outline_json=? WHERE id=?""",
            (json.dumps({"topics": ["Completed lesson", "Pending lesson"]}), course["id"]),
        )
        conn.execute(
            """INSERT INTO browser_learning_lessons(course_id, position, topic, status, content)
               VALUES (?, 1, 'Completed lesson', 'completed', 'durable completed content')""",
            (course["id"],),
        )
        conn.execute(
            """INSERT INTO browser_learning_lessons(course_id, position, topic, status, error)
               VALUES (?, 2, 'Pending lesson', 'running', 'process stopped')""",
            (course["id"],),
        )

    recovered_count = browser_learning.recover_interrupted_courses()
    recovered = browser_learning.get_course(course["id"])

    assert recovered_count == 1
    assert recovered["status"] == "interrupted"
    assert recovered["lessons"][0]["status"] == "completed"
    assert recovered["lessons"][0]["content"] == "durable completed content"
    assert recovered["lessons"][1]["status"] == "queued"


def test_dispatch_resumes_oldest_interrupted_course_and_drains_queue(monkeypatch, tmp_path):
    _init(tmp_path, monkeypatch)
    monkeypatch.setattr(browser_learning, "_ACTIVE_COURSE_ID", None)
    first = browser_learning.create_course("First interrupted", "http://127.0.0.1:9222", 2)
    with db.learning_connection() as conn:
        conn.execute("UPDATE browser_learning_courses SET status='interrupted' WHERE id=?", (first["id"],))
    second = browser_learning.create_course("Second interrupted", "http://127.0.0.1:9222", 2)
    with db.learning_connection() as conn:
        conn.execute("UPDATE browser_learning_courses SET status='interrupted' WHERE id=?", (second["id"],))

    launched = []
    executed = []

    class FakeThread:
        def __init__(self, *, target, args, name, daemon):
            self.target, self.args, self.name, self.daemon = target, args, name, daemon

        def start(self):
            launched.append(self)

    monkeypatch.setattr(browser_learning, "_start_course_worker_lease_heartbeat", lambda: None)
    monkeypatch.setattr(browser_learning.threading, "Thread", FakeThread)
    def complete_fake_course(course_id):
        executed.append(course_id)
        with db.learning_connection() as conn:
            conn.execute(
                "UPDATE browser_learning_courses SET status='completed' WHERE id=?",
                (course_id,),
            )

    monkeypatch.setattr(browser_learning, "run_course", complete_fake_course)

    first_dispatch = browser_learning.dispatch_next_course()
    assert first_dispatch == {"status": "running", "course_id": first["id"]}
    assert browser_learning.get_course(first["id"])["status"] == "running"
    assert browser_learning.get_course(second["id"])["status"] == "interrupted"
    assert browser_learning.dispatch_next_course() == {
        "status": "already_active", "course_id": first["id"]
    }

    launched[0].target(*launched[0].args)
    assert executed == [first["id"]]
    assert len(launched) == 2
    assert browser_learning.get_course(second["id"])["status"] == "running"

    launched[1].target(*launched[1].args)
    assert executed == [first["id"], second["id"]]
    assert browser_learning.dispatch_next_course() == {"status": "idle"}


def test_recovery_does_not_mark_a_live_in_process_course_interrupted(tmp_path, monkeypatch):
    _init(tmp_path, monkeypatch)
    course = browser_learning.create_course("Live course", "http://127.0.0.1:9222", 1)
    with db.learning_connection() as conn:
        conn.execute("UPDATE browser_learning_courses SET status='running' WHERE id=?", (course["id"],))
    monkeypatch.setattr(browser_learning, "_ACTIVE_COURSE_ID", course["id"])

    assert browser_learning.recover_interrupted_courses() == 0
    assert browser_learning.get_course(course["id"])["status"] == "running"


def test_dispatch_thread_start_failure_preserves_course_for_later_recovery(monkeypatch, tmp_path):
    _init(tmp_path, monkeypatch)
    monkeypatch.setattr(browser_learning, "_ACTIVE_COURSE_ID", None)
    course = browser_learning.create_course("Thread startup failure", "http://127.0.0.1:9222", 1)

    class FailingThread:
        def __init__(self, **kwargs):
            pass

        def start(self):
            raise RuntimeError("simulated thread start failure")

    monkeypatch.setattr(browser_learning, "_start_course_worker_lease_heartbeat", lambda: None)
    monkeypatch.setattr(browser_learning.threading, "Thread", FailingThread)
    result = browser_learning.dispatch_next_course()

    assert result == {"status": "interrupted", "course_id": course["id"]}
    assert browser_learning.get_course(course["id"])["status"] == "interrupted"
    assert browser_learning._ACTIVE_COURSE_ID is None


def test_browser_learning_worker_lease_blocks_another_owner_and_allows_expired_reclaim(tmp_path, monkeypatch):
    _init(tmp_path, monkeypatch)

    assert browser_learning._acquire_course_worker_lease("owner-a", now=1000.0) is True
    assert browser_learning._acquire_course_worker_lease("owner-b", now=1001.0) is False

    with db.learning_connection() as conn:
        conn.execute(
            "UPDATE browser_learning_worker_lease SET lease_until=? WHERE lease_name='browser-learning'",
            (1000.0,),
        )

    assert browser_learning._acquire_course_worker_lease("owner-b", now=1001.0) is True
    with db.learning_connection() as conn:
        row = conn.execute(
            "SELECT owner_token FROM browser_learning_worker_lease WHERE lease_name='browser-learning'"
        ).fetchone()
    assert row["owner_token"] == "owner-b"


def test_startup_recovery_does_not_rewrite_another_process_live_course(tmp_path, monkeypatch):
    _init(tmp_path, monkeypatch)
    course = browser_learning.create_course("Another process is running", "http://127.0.0.1:9222", 1)
    with db.learning_connection() as conn:
        conn.execute(
            "UPDATE browser_learning_courses SET status='running' WHERE id=?",
            (course["id"],),
        )
        conn.execute(
            """INSERT INTO browser_learning_worker_lease(lease_name, owner_token, lease_until)
               VALUES ('browser-learning', 'other-process', ?)""",
            (time.time() + 240.0,),
        )

    assert browser_learning.recover_interrupted_courses() == 0
    assert browser_learning.get_course(course["id"])["status"] == "running"


def test_dispatch_does_not_start_a_second_process_worker_when_lease_is_held(tmp_path, monkeypatch):
    _init(tmp_path, monkeypatch)
    course = browser_learning.create_course("Lease held", "http://127.0.0.1:9222", 1)
    with db.learning_connection() as conn:
        conn.execute(
            """INSERT INTO browser_learning_worker_lease(lease_name, owner_token, lease_until)
               VALUES ('browser-learning', 'other-process', ?)""",
            (time.time() + 240.0,),
        )

    result = browser_learning.dispatch_next_course()
    assert result == {"status": "lease_held"}
    assert browser_learning.get_course(course["id"])["status"] == "queued"


def test_worker_stops_before_persisting_when_lease_owner_changes(tmp_path, monkeypatch):
    _init(tmp_path, monkeypatch)
    assert browser_learning._acquire_course_worker_lease() is True
    with db.learning_connection() as conn:
        conn.execute(
            "UPDATE browser_learning_worker_lease SET owner_token='replacement-owner' WHERE lease_name='browser-learning'"
        )

    with pytest.raises(browser_learning.BrowserLearningLeaseLost):
        browser_learning._assert_course_worker_lease()
    assert browser_learning._WORKER_LEASE_LOST.is_set()


def test_run_course_does_not_change_status_after_lease_loss(tmp_path, monkeypatch):
    _init(tmp_path, monkeypatch)
    course = browser_learning.create_course("Lease lost before start", "http://127.0.0.1:9222", 1)
    browser_learning._WORKER_LEASE_LOST.set()

    browser_learning.run_course(course["id"])

    assert browser_learning.get_course(course["id"])["status"] == "queued"


def test_worker_identity_is_regenerated_after_process_fork(tmp_path, monkeypatch):
    _init(tmp_path, monkeypatch)
    old_pid = browser_learning._WORKER_OWNER_PID
    old_token = browser_learning._WORKER_OWNER_TOKEN
    monkeypatch.setattr(browser_learning.os, "getpid", lambda: old_pid + 1)
    monkeypatch.setattr(browser_learning, "_ACTIVE_COURSE_ID", 99)

    browser_learning._ensure_process_identity()

    assert browser_learning._WORKER_OWNER_PID == old_pid + 1
    assert browser_learning._WORKER_OWNER_TOKEN != old_token
    assert browser_learning._ACTIVE_COURSE_ID is None
