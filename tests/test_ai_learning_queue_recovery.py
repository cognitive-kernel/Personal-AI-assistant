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


def test_paused_topic_run_is_skipped_until_topic_is_resumed(isolated_store, monkeypatch):
    course = service.create_course(title="صف سرفصل مکث‌شده")
    topic = service.add_topic(course["id"], "Paused topic")
    service.decide_topics([topic["id"]], "approved")
    run = service.create_run(
        course["id"], run_type="lesson", topic_ids=[topic["id"]],
        selection_mode="selected", request_key="paused-topic-queued-run",
    )
    service.set_topic_paused(topic["id"], True)

    assert service.get_oldest_queued_run() is None
    assert service.get_oldest_resumable_run() is None
    with pytest.raises(ValueError, match="paused topic"):
        service.start_run(run["id"])
    assert service.get_run(run["id"])["status"] == "queued"

    launched = []

    class FakeThread:
        def __init__(self, *, target, args, name, daemon):
            self.args = args
        def start(self):
            launched.append(self.args)

    monkeypatch.setattr(browser_automation.threading, "Thread", FakeThread)
    skipped = browser_automation._dispatch_next_persisted_run()
    assert skipped["status"] == "idle"
    assert launched == []

    browser_automation.set_topic_paused(topic["id"], False)
    assert len(launched) == 1
    assert launched[0][0] == run["id"]
    assert service.get_run(run["id"])["status"] == "running"


def test_pausing_active_topic_requeues_run_and_resume_dispatches_it(isolated_store, monkeypatch):
    course = service.create_course(title="مکث اجرای فعال")
    topic = service.add_topic(course["id"], "Active topic")
    service.decide_topics([topic["id"]], "approved")
    run = service.create_run(
        course["id"], run_type="lesson", topic_ids=[topic["id"]],
        selection_mode="selected", request_key="active-topic-pause",
    )
    service.start_run(run["id"])
    cancel_event = threading.Event()
    monkeypatch.setattr(browser_automation, "_ACTIVE", True)
    monkeypatch.setattr(browser_automation, "_ACTIVE_RUN_ID", run["id"])
    monkeypatch.setitem(browser_automation._CANCEL_EVENTS, run["id"], cancel_event)

    browser_automation.set_topic_paused(topic["id"], True)

    assert cancel_event.is_set()
    assert service.get_run(run["id"])["status"] == "queued"
    assert next(item for item in service.get_course(course["id"])["topics"] if item["id"] == topic["id"])["processing_status"] == "paused"
    assert service.get_oldest_queued_run() is None

    # The active worker's finalizer clears its marker before dispatching the queue.
    monkeypatch.setattr(browser_automation, "_ACTIVE", False)
    monkeypatch.setattr(browser_automation, "_ACTIVE_RUN_ID", None)
    browser_automation._CANCEL_EVENTS.pop(run["id"], None)
    launched = []

    class FakeThread:
        def __init__(self, *, target, args, name, daemon):
            self.args = args
        def start(self):
            launched.append(self.args)

    monkeypatch.setattr(browser_automation.threading, "Thread", FakeThread)
    browser_automation.set_topic_paused(topic["id"], False)

    assert len(launched) == 1
    assert launched[0][0] == run["id"]
    assert service.get_run(run["id"])["status"] == "running"


def test_lesson_completion_racing_with_topic_pause_is_requeued_without_saving(isolated_store):
    course = service.create_course(title="رقابت مکث و تکمیل")
    topic = service.add_topic(course["id"], "Paused before completion")
    service.decide_topics([topic["id"]], "approved")
    run = service.create_run(
        course["id"], run_type="lesson", topic_ids=[topic["id"]],
        selection_mode="selected", request_key="pause-completion-race",
    )
    service.start_run(run["id"])
    service.set_topic_paused(topic["id"], True)

    result = service.complete_run(run["id"], "متن کامل درس با توضیحات کافی و منبع https://example.com/reference")

    assert result["status"] == "queued"
    assert next(item for item in service.get_course(course["id"])["topics"] if item["id"] == topic["id"])["processing_status"] == "paused"
    assert service.list_lessons(topic["id"]) == []


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


def test_manual_resume_materializes_legacy_lesson_followups_before_worker_start(isolated_store, monkeypatch):
    course = service.create_course(title="بازیابی دستهٔ قدیمی")
    topics = [service.add_topic(course["id"], f"موضوع {index}") for index in range(3)]
    service.decide_topics([topic["id"] for topic in topics], "approved")
    legacy = service.create_run(
        course["id"], run_type="lesson", topic_ids=[topics[0]["id"]],
        selection_mode="selected", request_key="legacy-in-memory-followups",
    )
    service.start_run(legacy["id"])
    launched = []

    class FakeThread:
        def __init__(self, *, target, args, name, daemon):
            self.args = args
        def start(self):
            launched.append(self.args)

    monkeypatch.setattr(browser_automation.threading, "Thread", FakeThread)
    resumed = browser_automation.resume_learning()

    assert resumed["status"] == "resumed"
    assert resumed["run_id"] == legacy["id"]
    assert resumed["queued_topic_count"] == 3
    assert len(service.list_course_runs(course["id"], 20)) == 3
    assert service.has_queued_run_for_course(course["id"]) is True
    assert len(launched) == 1
    assert len(launched[0]) == 4  # follow-ups are durable, not worker arguments

    monkeypatch.setattr(browser_automation, "_ACTIVE", False)
    monkeypatch.setattr(browser_automation, "_ACTIVE_RUN_ID", None)
    monkeypatch.setattr(browser_automation, "_ACTIVE_COURSE_ID", None)
    monkeypatch.setattr(browser_automation, "_CANCEL_EVENTS", {})
    recovered = browser_automation.recover_pending_runs()

    assert recovered["status"] == "running"
    assert recovered["run_id"] == legacy["id"]
    assert len(launched) == 2
    run_ids = [item["id"] for item in service.list_course_runs(course["id"], 20)]
    statuses = [service.get_run(run_id)["status"] for run_id in run_ids]
    assert statuses.count("running") == 1
    assert statuses.count("queued") == 2


def test_manual_resume_persists_all_pending_topics_outside_dashboard_course_limit(isolated_store, monkeypatch):
    first_course = service.create_course(title="دورهٔ قدیمی با چند سرفصل ناتمام")
    topics = [service.add_topic(first_course["id"], f"سرفصل خارج از صفحه {index}") for index in range(3)]
    service.decide_topics([topic["id"] for topic in topics], "approved")
    for index in range(100):
        service.create_course(title=f"دورهٔ جدید {index:03d}")
    assert all(
        int(item["id"]) != int(first_course["id"])
        for item in service.list_courses(100)
    )
    assert service.get_oldest_resumable_run() is None
    launched = []

    class FakeThread:
        def __init__(self, *, target, args, name, daemon):
            self.args = args
        def start(self):
            launched.append(self.args)

    monkeypatch.setattr(browser_automation.threading, "Thread", FakeThread)
    result = browser_automation.resume_learning()

    assert result["status"] == "resumed"
    assert result["course_id"] == first_course["id"]
    assert result["queued_topic_count"] == 3
    assert len(launched) == 1
    assert len(service.list_course_runs(first_course["id"], 20)) == 3
    assert service.has_queued_run_for_course(first_course["id"]) is True
    run_ids = [item["id"] for item in service.list_course_runs(first_course["id"], 20)]
    assert {
        int(service.get_run(run_id)["topics"][0]["topic_id"])
        for run_id in run_ids
    } == {int(topic["id"]) for topic in topics}

    # Simulate process loss and verify every not-yet-started topic survives in SQLite.
    monkeypatch.setattr(browser_automation, "_ACTIVE", False)
    monkeypatch.setattr(browser_automation, "_ACTIVE_RUN_ID", None)
    monkeypatch.setattr(browser_automation, "_ACTIVE_COURSE_ID", None)
    monkeypatch.setattr(browser_automation, "_CANCEL_EVENTS", {})
    recovered = browser_automation.recover_pending_runs()

    assert recovered["status"] == "running"
    assert recovered["run_id"] == result["run_id"]
    assert len(launched) == 2
    statuses = [
        service.get_run(run_id)["status"]
        for run_id in run_ids
    ]
    assert statuses.count("running") == 1
    assert statuses.count("queued") == 2


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

def test_transient_browser_failure_is_scheduled_with_partial_response_preserved(isolated_store, monkeypatch):
    course = service.create_course(title="بازیابی خودکار خطای موقت")
    run = service.create_run(course["id"], run_type="outline", request_key="transient-worker-retry")
    service.start_run(run["id"])

    def fail_capture(*_args, **_kwargs):
        raise browser_automation.BrowserAutomationError(
            "chatgpt_network_error", "temporary network interruption",
            partial_response="partial captured response",
        )

    monkeypatch.setattr(browser_automation, "_capture_with_automatic_retry", fail_capture)
    monkeypatch.setattr(browser_automation, "_close_browser_session", lambda: None)
    monkeypatch.setattr(browser_automation, "_dispatch_next_persisted_run", lambda: {"status": "idle"})
    monkeypatch.setattr(browser_automation, "_log_event", lambda *_args, **_kwargs: None)

    browser_automation._worker(
        run["id"], course["id"], run["prompt_text"], threading.Event()
    )

    saved = service.get_run(run["id"])
    assert saved["status"] == "queued"
    assert saved["automatic_retry_count"] == 1
    assert saved["next_retry_at"] is not None
    assert saved["error_code"] == "chatgpt_network_error"
    assert saved["response_text"] == "partial captured response"
    assert service.get_oldest_queued_run() is None


def test_manual_provider_gate_does_not_enter_automatic_retry_queue(isolated_store, monkeypatch):
    course = service.create_course(title="توقف برای تأیید انسانی")
    run = service.create_run(course["id"], run_type="outline", request_key="human-gate-no-retry")
    service.start_run(run["id"])

    def fail_capture(*_args, **_kwargs):
        raise browser_automation.BrowserAutomationError(
            "human_verification_required", "operator must complete verification"
        )

    monkeypatch.setattr(browser_automation, "_capture_with_automatic_retry", fail_capture)
    monkeypatch.setattr(browser_automation, "_close_browser_session", lambda: None)
    monkeypatch.setattr(browser_automation, "_dispatch_next_persisted_run", lambda: {"status": "idle"})
    monkeypatch.setattr(browser_automation, "_log_event", lambda *_args, **_kwargs: None)

    browser_automation._worker(
        run["id"], course["id"], run["prompt_text"], threading.Event()
    )

    saved = service.get_run(run["id"])
    assert saved["status"] == "needs_user_action"
    assert saved["automatic_retry_count"] == 0
    assert saved["next_retry_at"] is None


def test_scheduled_retry_waits_until_due_then_dispatches(isolated_store, monkeypatch):
    course = service.create_course(title="اجرای مجدد پس از مهلت")
    run = service.create_run(course["id"], run_type="outline", request_key="retry-wait")
    service.start_run(run["id"])
    scheduled = service.schedule_run_retry(
        run["id"], "chatgpt_network_error", "network unavailable",
        delay_seconds=300,
    )
    assert scheduled is not None
    assert service.get_oldest_queued_run() is None
    assert service.get_learning_control()["retry_pending"] is True

    monkeypatch.setattr(browser_automation, "_ACTIVE", False)
    monkeypatch.setattr(browser_automation, "_SHUTTING_DOWN", False)
    assert browser_automation._dispatch_next_persisted_run()["status"] == "retry_scheduled"

    from app.ai_learning.db import connection
    with connection() as conn:
        conn.execute(
            "UPDATE runs SET next_retry_at=datetime(CURRENT_TIMESTAMP,'-1 second') WHERE id=?",
            (run["id"],),
        )
        conn.execute(
            "UPDATE ai_learning_control SET retry_after=datetime(CURRENT_TIMESTAMP,'-1 second') WHERE id=1"
        )

    assert service.get_learning_control()["retry_pending"] is False
    assert service.get_oldest_queued_run()["id"] == run["id"]
    launched = []

    class FakeThread:
        def __init__(self, *, target, args, name, daemon):
            self.args = args
        def start(self):
            launched.append(self.args)

    monkeypatch.setattr(browser_automation, "_ACTIVE", False)
    monkeypatch.setattr(browser_automation, "_SHUTTING_DOWN", False)
    monkeypatch.setattr(browser_automation.threading, "Thread", FakeThread)
    dispatched = browser_automation._dispatch_next_persisted_run()

    assert dispatched["status"] == "running"
    assert dispatched["run_id"] == run["id"]
    assert launched[0][0] == run["id"]
    assert service.get_run(run["id"])["status"] == "running"
    assert service.get_run(run["id"])["next_retry_at"] is None


def test_scheduled_retry_budget_is_bounded_and_manual_resume_resets_it(isolated_store):
    course = service.create_course(title="محدودیت تعداد تلاش خودکار")
    run = service.create_run(course["id"], run_type="outline", request_key="retry-budget")
    service.start_run(run["id"])

    for _ in range(service.MAX_SCHEDULED_RUN_RETRIES):
        scheduled = service.schedule_run_retry(
            run["id"], "chatgpt_network_error", "temporary network failure",
            delay_seconds=1,
        )
        assert scheduled is not None
        service.start_run(run["id"])

    assert service.schedule_run_retry(
        run["id"], "chatgpt_network_error", "still unavailable",
        delay_seconds=1,
    ) is None
    service.mark_run_needs_user_action(run["id"], "chatgpt_network_error", "retry budget exhausted")
    recovered = service.requeue_run_for_resume(
        run["id"], "manual_resume_recovery", reset_retry_budget=True
    )
    assert recovered["status"] == "queued"
    assert recovered["automatic_retry_count"] == 0
    assert recovered["next_retry_at"] is None

def test_provider_rate_limit_skips_short_retries_and_uses_delayed_schedule(isolated_store, monkeypatch):
    course = service.create_course(title="محدودیت درخواست ارائه‌دهنده")
    run = service.create_run(course["id"], run_type="outline", request_key="rate-limit-delay")
    attempts = []

    def rate_limited(*_args, **_kwargs):
        attempts.append(1)
        raise browser_automation.BrowserAutomationError(
            "provider_rate_limited", "try again later"
        )

    monkeypatch.setattr(browser_automation, "_browser_capture", rate_limited)
    monkeypatch.setattr(browser_automation, "_AUTO_RETRY_MAX_ATTEMPTS", 3)
    monkeypatch.setattr(browser_automation, "_AUTO_RETRY_INITIAL_DELAY_SECONDS", 0)

    with pytest.raises(browser_automation.BrowserAutomationError) as error:
        browser_automation._capture_with_automatic_retry(
            run["prompt_text"], threading.Event(), run_id=run["id"], phase="test"
        )

    assert error.value.code == "provider_rate_limited"
    assert len(attempts) == 1
    service.start_run(run["id"])
    scheduled = browser_automation._schedule_transient_retry(run["id"], error.value)
    assert scheduled is not None
    assert scheduled["automatic_retry_count"] == 1
    assert scheduled["next_retry_at"] is not None

def test_cancelled_scheduled_retry_cannot_be_dispatched(isolated_store, monkeypatch):
    course = service.create_course(title="لغو تلاش زمان‌بندی‌شده")
    run = service.create_run(course["id"], run_type="outline", request_key="cancel-delayed-retry")
    service.start_run(run["id"])
    service.schedule_run_retry(
        run["id"], "chatgpt_network_error", "temporary network failure",
        delay_seconds=60,
    )

    cancelled = service.cancel_run(run["id"])
    assert cancelled["status"] == "cancelled"
    assert cancelled["next_retry_at"] is None
    assert service.get_learning_control()["retry_after"] is None
    assert service.get_learning_control()["retry_pending"] is False

    monkeypatch.setattr(browser_automation, "_ACTIVE", False)
    monkeypatch.setattr(browser_automation, "_SHUTTING_DOWN", False)
    assert browser_automation._dispatch_next_persisted_run()["status"] == "idle"

def test_manual_resume_resets_exhausted_retry_budget(isolated_store, monkeypatch):
    course = service.create_course(title="بازیابی دستی پس از تلاش‌های خودکار")
    run = service.create_run(course["id"], run_type="outline", request_key="manual-reset-retry-budget")
    service.start_run(run["id"])
    for _ in range(service.MAX_SCHEDULED_RUN_RETRIES):
        service.schedule_run_retry(
            run["id"], "chatgpt_network_error", "temporary network failure",
            delay_seconds=1,
        )
        service.start_run(run["id"])
    service.mark_run_needs_user_action(
        run["id"], "chatgpt_network_error", "automatic retries exhausted"
    )

    launched = []

    class FakeThread:
        def __init__(self, *, target, args, name, daemon):
            self.args = args
        def start(self):
            launched.append(self.args)

    monkeypatch.setattr(browser_automation.threading, "Thread", FakeThread)
    resumed = browser_automation.resume_learning()

    saved = service.get_run(run["id"])
    assert resumed["status"] == "resumed"
    assert saved["status"] == "running"
    assert saved["automatic_retry_count"] == 0
    assert saved["next_retry_at"] is None
    assert len(launched) == 1

def test_global_retry_gate_prevents_queue_cascade_after_provider_failure(isolated_store, monkeypatch):
    course = service.create_course(title="صف با مکث سراسری خطای ارائه‌دهنده")
    delayed = service.create_run(course["id"], run_type="outline", request_key="future-retry-first")
    service.start_run(delayed["id"])
    service.schedule_run_retry(
        delayed["id"], "chatgpt_network_error", "temporary network failure",
        delay_seconds=300,
    )
    ready = service.create_run(course["id"], run_type="outline", request_key="ready-second")

    selected = service.get_oldest_queued_run()
    assert selected is not None
    assert selected["id"] == ready["id"]
    assert service.get_learning_control()["retry_pending"] is True

    monkeypatch.setattr(browser_automation, "_ACTIVE", False)
    monkeypatch.setattr(browser_automation, "_SHUTTING_DOWN", False)
    result = browser_automation._dispatch_next_persisted_run()
    assert result["status"] == "retry_scheduled"
    assert service.get_run(ready["id"])["status"] == "queued"
