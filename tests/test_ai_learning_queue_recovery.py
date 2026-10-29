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
    monkeypatch.setattr(browser_automation, "_SHUTTING_DOWN", False)
    return tmp_path


def test_startup_recovers_oldest_persisted_run_without_creating_new_work(isolated_store, monkeypatch):
    course = service.create_course(title="بازیابی پایدار صف")
    first = service.create_run(course["id"], run_type="outline", request_key="persisted-first")
    service.start_run(first["id"])  # Simulate a process that died while the run was active.
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


def test_browser_capture_retry_is_bounded_after_repeated_failure(isolated_store, monkeypatch):
    course = service.create_course(title="توقف تلاش‌های تکراری")
    run = service.create_run(course["id"], run_type="outline", request_key="retry-exhausted")
    attempts = []

    def always_fail(prompt, cancel_event, checkpoint):
        attempts.append(prompt)
        raise browser_automation.BrowserAutomationError(
            "playwright_browser_error", "Chrome failed to launch"
        )

    monkeypatch.setattr(browser_automation, "_browser_capture", always_fail)
    monkeypatch.setattr(browser_automation, "_AUTO_RETRY_INITIAL_DELAY_SECONDS", 0)
    monkeypatch.setattr(browser_automation, "_AUTO_RETRY_MAX_ATTEMPTS", 3)

    with pytest.raises(browser_automation.BrowserAutomationError) as error:
        browser_automation._capture_with_automatic_retry(
            run["prompt_text"], threading.Event(), run_id=run["id"], phase="test"
        )

    assert len(attempts) == 3
    assert error.value.code == "playwright_browser_error"
    assert "پس از 3 تلاش خودکار" in str(error.value)


def test_dispatch_does_not_restart_runs_needing_user_action(isolated_store, monkeypatch):
    course = service.create_course(title="عدم اجرای مجدد خودکار")
    run = service.create_run(course["id"], run_type="outline", request_key="needs-action-stays")
    service.start_run(run["id"])
    service.mark_run_needs_user_action(run["id"], "chrome_not_found", "Chrome startup failed")
    launched = []

    class FakeThread:
        def __init__(self, **kwargs):
            launched.append(kwargs)

        def start(self):
            raise AssertionError("needs_user_action must not be automatically restarted")

    monkeypatch.setattr(browser_automation.threading, "Thread", FakeThread)
    result = browser_automation._dispatch_next_persisted_run()

    assert result == {"status": "idle", "active": False}
    assert launched == []
    assert service.get_run(run["id"])["status"] == "needs_user_action"


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


def test_shutdown_signals_workers_and_blocks_queue_dispatch(isolated_store, monkeypatch):
    course = service.create_course(title="توقف مرورگر با خاموش‌شدن سرور")
    run = service.create_run(course["id"], run_type="outline", request_key="shutdown-run")
    cancel_event = threading.Event()
    monkeypatch.setattr(browser_automation, "_CANCEL_EVENTS", {run["id"]: cancel_event})
    launched = []

    class FakeThread:
        def __init__(self, **kwargs):
            launched.append(kwargs)

        def start(self):
            raise AssertionError("shutdown must not launch another browser worker")

    monkeypatch.setattr(browser_automation.threading, "Thread", FakeThread)
    result = browser_automation.stop_browser_automation()
    dispatched = browser_automation._dispatch_next_persisted_run()

    assert result["status"] == "shutting_down"
    assert cancel_event.is_set()
    assert dispatched == {"status": "shutting_down", "active": False}
    assert launched == []


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


def test_startup_recovers_oldest_run_beyond_recent_run_page(isolated_store, monkeypatch):
    course = service.create_course(title="بازیابی کار قدیمی‌تر از صفحهٔ اخیر")
    runs = [
        service.create_run(
            course["id"], run_type="outline", request_key=f"older-than-page-{index}"
        )
        for index in range(51)
    ]
    service.start_run(runs[0]["id"])
    launched = []

    class FakeThread:
        def __init__(self, *, target, args, name, daemon):
            self.target, self.args, self.name, self.daemon = target, args, name, daemon

        def start(self):
            launched.append(self)

    monkeypatch.setattr(browser_automation.threading, "Thread", FakeThread)
    result = browser_automation.recover_pending_runs()

    assert result["status"] == "running"
    assert result["run_id"] == runs[0]["id"]
    assert launched[0].args[0] == runs[0]["id"]
    assert service.get_run(runs[0]["id"])["status"] == "running"


def test_dispatch_finds_queued_run_outside_dashboard_course_limit(isolated_store, monkeypatch):
    courses = [
        service.create_course(title=f"دورهٔ صف سراسری {index:03d}")
        for index in range(101)
    ]
    oldest_course = courses[0]
    run = service.create_run(
        oldest_course["id"], run_type="outline", request_key="outside-dashboard-course-page"
    )
    # The dashboard API is intentionally capped at 100 courses; queue semantics
    # must not inherit that presentation-layer limit.
    assert all(
        int(item["id"]) != int(oldest_course["id"])
        for item in service.list_courses(100)
    )
    launched = []

    class FakeThread:
        def __init__(self, *, target, args, name, daemon):
            self.target, self.args, self.name, self.daemon = target, args, name, daemon

        def start(self):
            launched.append(self)

    monkeypatch.setattr(browser_automation.threading, "Thread", FakeThread)
    result = browser_automation._dispatch_next_persisted_run()

    assert result["status"] == "running"
    assert result["run_id"] == run["id"]
    assert launched[0].args[0] == run["id"]


def test_startup_recovery_does_not_requeue_an_active_in_process_run(isolated_store, monkeypatch):
    course = service.create_course(title="بازیابی نباید کار فعال را بازصف‌بندی کند")
    run = service.create_run(course["id"], run_type="outline", request_key="active-worker-not-requeued")
    service.start_run(run["id"])
    monkeypatch.setattr(browser_automation, "_ACTIVE", True)
    monkeypatch.setattr(browser_automation, "_ACTIVE_RUN_ID", run["id"])
    monkeypatch.setattr(browser_automation, "_ACTIVE_COURSE_ID", course["id"])

    result = browser_automation.recover_pending_runs()

    assert result["status"] == "already_active"
    assert result["run_id"] == run["id"]
    assert service.get_run(run["id"])["status"] == "running"


def test_runtime_watchdog_recovers_orphaned_run_after_worker_disappears(isolated_store, monkeypatch):
    course = service.create_course(title="بازیابی دورهٔ رهاشده")
    run = service.create_run(course["id"], run_type="outline", request_key="runtime-orphan")
    service.start_run(run["id"])
    launched = []

    class FakeThread:
        def __init__(self, *, target, args, name, daemon):
            self.target, self.args, self.name, self.daemon = target, args, name, daemon

        def start(self):
            launched.append(self)

    monkeypatch.setattr(browser_automation.threading, "Thread", FakeThread)
    result = browser_automation.recover_orphaned_runs()

    assert result["status"] == "running"
    assert result["run_id"] == run["id"]
    assert len(launched) == 1
    assert launched[0].args[0] == run["id"]
    assert service.get_run(run["id"])["status"] == "running"


def test_runtime_watchdog_does_not_requeue_an_active_worker(isolated_store, monkeypatch):
    course = service.create_course(title="دورهٔ فعال")
    run = service.create_run(course["id"], run_type="outline", request_key="runtime-active")
    service.start_run(run["id"])
    monkeypatch.setattr(browser_automation, "_ACTIVE", True)
    monkeypatch.setattr(browser_automation, "_ACTIVE_RUN_ID", run["id"])

    result = browser_automation.recover_orphaned_runs()

    assert result["status"] == "already_active"
    assert service.get_run(run["id"])["status"] == "running"


def test_runtime_watchdog_never_clears_shutdown_or_restarts_chrome(isolated_store, monkeypatch):
    course = service.create_course(title="دوره هنگام shutdown")
    run = service.create_run(course["id"], run_type="outline", request_key="runtime-shutdown")
    service.start_run(run["id"])
    monkeypatch.setattr(browser_automation, "_SHUTTING_DOWN", True)

    result = browser_automation.recover_orphaned_runs()

    assert result == {"status": "shutting_down", "active": False}
    assert browser_automation._SHUTTING_DOWN is True
    assert service.get_run(run["id"])["status"] == "running"


def test_learning_runtime_maintenance_invokes_orphan_recovery(monkeypatch):
    from app import main

    calls = []
    monkeypatch.setattr(main.learning, "maintain_factual_knowledge", lambda: calls.append("knowledge"))
    monkeypatch.setattr(
        main.browser_learning,
        "recover_interrupted_courses",
        lambda: calls.append("browser_recovery") or 0,
    )
    monkeypatch.setattr(
        main.browser_learning,
        "dispatch_next_course",
        lambda: calls.append("browser_dispatch") or {"status": "idle"},
    )
    monkeypatch.setattr(main.ai_learning_config, "is_enabled", lambda: True)
    monkeypatch.setattr(
        main.ai_learning_browser_automation,
        "recover_orphaned_runs",
        lambda: calls.append("recovery") or {"status": "idle", "active": False},
    )

    main._maintain_learning_runtime()

    assert calls == ["knowledge", "browser_recovery", "browser_dispatch", "recovery"]



def test_manual_resume_finds_run_outside_dashboard_course_limit(isolated_store, monkeypatch):
    courses = [
        service.create_course(title=f"بازیابی دستی سراسری {index:03d}")
        for index in range(101)
    ]
    oldest_course = courses[0]
    run = service.create_run(
        oldest_course["id"], run_type="outline", request_key="manual-resume-outside-course-page"
    )
    assert all(
        int(item["id"]) != int(oldest_course["id"])
        for item in service.list_courses(100)
    )
    launched = []

    class FakeThread:
        def __init__(self, *, target, args, name, daemon):
            self.target, self.args, self.name, self.daemon = target, args, name, daemon

        def start(self):
            launched.append(self)

    monkeypatch.setattr(browser_automation.threading, "Thread", FakeThread)
    result = browser_automation.resume_learning()

    assert result["status"] == "resumed"
    assert result["run_id"] == run["id"]
    assert result["course_id"] == oldest_course["id"]
    assert len(launched) == 1
    assert launched[0].args[0] == run["id"]
    assert service.get_run(run["id"])["status"] == "running"


def test_dispatch_requeues_run_when_worker_thread_cannot_start(isolated_store, monkeypatch):
    course = service.create_course(title="بازیابی خطای ایجاد worker")
    run = service.create_run(course["id"], run_type="outline", request_key="thread-start-fails")
    attempts = []

    class FailingThread:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def start(self):
            attempts.append(self.kwargs)
            raise RuntimeError("simulated thread start failure")

    monkeypatch.setattr(browser_automation.threading, "Thread", FailingThread)

    with pytest.raises(browser_automation.BrowserAutomationError) as error:
        browser_automation._dispatch_next_persisted_run()

    assert error.value.code == "worker_thread_start_failed"
    assert len(attempts) == 1
    assert service.get_run(run["id"])["status"] == "queued"
    assert browser_automation._ACTIVE is False
    assert browser_automation._ACTIVE_RUN_ID is None
    assert browser_automation._ACTIVE_COURSE_ID is None
    assert run["id"] not in browser_automation._CANCEL_EVENTS

    launched = []

    class WorkingThread:
        def __init__(self, *, target, args, name, daemon):
            self.target, self.args, self.name, self.daemon = target, args, name, daemon

        def start(self):
            launched.append(self)

    monkeypatch.setattr(browser_automation.threading, "Thread", WorkingThread)
    recovered = browser_automation._dispatch_next_persisted_run()

    assert recovered["status"] == "running"
    assert recovered["run_id"] == run["id"]
    assert len(launched) == 1
    assert service.get_run(run["id"])["status"] == "running"
