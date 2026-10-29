from __future__ import annotations

import threading

import pytest

from app.ai_learning import browser_automation, service
from app.ai_learning.db import initialize_store


@pytest.fixture
def isolated_store(tmp_path, monkeypatch):
    monkeypatch.setenv("AI_LEARNING_DB_PATH", str(tmp_path / "ai_learning.db"))
    initialize_store()
    monkeypatch.setattr(browser_automation, "_ACTIVE", False)
    monkeypatch.setattr(browser_automation, "_ACTIVE_RUN_ID", None)
    monkeypatch.setattr(browser_automation, "_ACTIVE_COURSE_ID", None)
    monkeypatch.setattr(browser_automation, "_CANCEL_EVENTS", {})
    return tmp_path


def test_startup_recovers_oldest_persisted_run_without_creating_new_work(isolated_store, monkeypatch):
    course = service.create_course(title="بازیابی پایدار صف")
    first = service.create_run(course["id"], run_type="outline", request_key="persisted-first")
    second = service.create_run(course["id"], run_type="outline", request_key="persisted-second")
    launched = []

    class FakeThread:
        def __init__(self, *, target, args, name, daemon):
            self.target, self.args, self.name, self.daemon = target, args, name, daemon

        def start(self):
            launched.append(self)

    monkeypatch.setattr(browser_automation.threading, "Thread", FakeThread)
    recovered = browser_automation.recover_pending_runs()

    assert recovered["status"] == "running"
    assert recovered["run_id"] == first["id"]
    assert len(launched) == 1
    assert launched[0].args[0] == first["id"]
    assert service.get_run(first["id"])["status"] == "running"
    assert service.get_run(second["id"])["status"] == "queued"
    # Recovery must not create a fresh run or automatically invent lessons.
    assert len(service.list_course_runs(course["id"], 20)) == 2


def test_startup_recovery_respects_durable_global_pause(isolated_store, monkeypatch):
    course = service.create_course(title="صف مکث‌شده")
    run = service.create_run(course["id"], run_type="outline", request_key="paused-run")
    service.set_learning_paused(True, "test pause")
    launched = []

    class FakeThread:
        def __init__(self, **kwargs):
            launched.append(kwargs)

        def start(self):
            raise AssertionError("paused queue must not launch a worker")

    monkeypatch.setattr(browser_automation.threading, "Thread", FakeThread)
    result = browser_automation.recover_pending_runs()

    assert result["status"] == "paused"
    assert launched == []
    assert service.get_run(run["id"])["status"] == "queued"


def test_queue_dispatch_does_not_start_second_worker_when_one_is_active(isolated_store, monkeypatch):
    course = service.create_course(title="جلوگیری از اجرای هم‌زمان")
    run = service.create_run(course["id"], run_type="outline", request_key="already-active")
    monkeypatch.setattr(browser_automation, "_ACTIVE", True)
    monkeypatch.setattr(browser_automation, "_ACTIVE_RUN_ID", 123)
    result = browser_automation._dispatch_next_persisted_run()
    assert result == {"status": "already_active", "active": True, "run_id": 123}
    assert service.get_run(run["id"])["status"] == "queued"


def test_browser_capture_retries_after_failure_until_success(isolated_store, monkeypatch):
    course = service.create_course(title="بازیابی خودکار خطای مرورگر")
    run = service.create_run(course["id"], run_type="outline", request_key="retry-browser")
    attempts = []

    def fake_capture(prompt, cancel_event, checkpoint):
        attempts.append(prompt)
        if len(attempts) == 1:
            raise browser_automation.BrowserAutomationError(
                "playwright_browser_error", "temporary Chrome startup failure"
            )
        return "recovered response"

    monkeypatch.setattr(browser_automation, "_browser_capture", fake_capture)
    monkeypatch.setattr(browser_automation, "_AUTO_RETRY_INITIAL_DELAY_SECONDS", 0)
    result = browser_automation._capture_with_automatic_retry(
        run["prompt_text"], threading.Event(), run_id=run["id"], phase="test"
    )

    assert result == "recovered response"
    assert len(attempts) == 2


def test_browser_capture_retry_stops_when_manually_cancelled(isolated_store, monkeypatch):
    course = service.create_course(title="توقف دستی بازیابی")
    run = service.create_run(course["id"], run_type="outline", request_key="manual-stop-retry")
    cancel_event = threading.Event()
    cancel_event.set()

    def unexpected_capture(*args, **kwargs):
        raise AssertionError("browser must not launch after a manual stop")

    monkeypatch.setattr(browser_automation, "_browser_capture", unexpected_capture)
    with pytest.raises(browser_automation.BrowserAutomationError) as error:
        browser_automation._capture_with_automatic_retry(
            run["prompt_text"], cancel_event, run_id=run["id"], phase="test"
        )

    assert error.value.code == "run_cancelled"


def test_requeue_recovers_needs_user_action_but_never_resurrects_cancelled_run(isolated_store):
    course = service.create_course(title="بازیابی وضعیت نیازمند اقدام")
    run = service.create_run(course["id"], run_type="outline", request_key="needs-action-requeue")
    service.start_run(run["id"])
    service.mark_run_needs_user_action(
        run["id"], "chrome_not_found", "Chrome startup failed"
    )

    recovered = service.requeue_run_for_resume(run["id"], "test")
    assert recovered["status"] == "queued"
    assert recovered["error_code"] is None

    cancelled = service.create_run(course["id"], run_type="outline", request_key="cancelled-not-resurrected")
    service.cancel_run(cancelled["id"])
    unchanged = service.requeue_run_for_resume(cancelled["id"], "test")
    assert unchanged["status"] == "cancelled"
