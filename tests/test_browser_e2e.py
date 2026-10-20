"""Small real-browser smoke tests for the local dashboard.

The regular unit suite skips these unless E2E_BASE_URL is explicitly configured.
"""
import json
import os
from pathlib import Path
from datetime import datetime, timedelta, timezone

import pytest

playwright_api = pytest.importorskip("playwright.sync_api")
sync_playwright = playwright_api.sync_playwright


def test_dashboard_loads_and_core_panels_work_without_a_live_model():
    base_url = os.environ.get("E2E_BASE_URL")
    if not base_url:
        pytest.skip("Set E2E_BASE_URL to run browser smoke tests.")
    browser_errors = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page()
        page.on("pageerror", lambda error: browser_errors.append(str(error)))
        response = page.goto(base_url, wait_until="domcontentloaded")
        assert response is not None and response.status == 200
        assert page.get_by_role("heading", name="دستیار هوش مصنوعی شخصی").is_visible()
        assert page.locator("#chat-form").count() == 1
        assert page.locator("#task-manager-section").is_visible()
        assert page.locator("#experience-memory-section").is_visible()

        storage_output = page.locator("#storage-health-output")
        page.locator("#refresh-storage-health").click()
        page.wait_for_function(
            """() => {
                const node = document.querySelector('#storage-health-output');
                return node && node.textContent.trim() &&
                    !node.textContent.includes('وضعیت ذخیره‌سازی هنوز بررسی نشده است') &&\n                    !node.textContent.includes('در حال بررسی فقط‌خواندنی سلامت ذخیره‌سازی');
            }""",
            timeout=15000,
        )
        storage_result = json.loads(storage_output.inner_text())
        assert storage_result["read_only"] is True
        assert storage_result["repair_performed"] is False
        assert {item["name"] for item in storage_result["databases"]} == {
            "settings", "learning", "cloud_resources"
        }
        assert all(item["status"] == "ok" for item in storage_result["databases"])

        # Exercise the real confirmation dialogs and the non-destructive
        # backup -> staging-restore workflow against disposable CI databases.
        page.on("dialog", lambda dialog: dialog.accept())
        page.locator("#create-storage-backup").click()
        page.wait_for_function(
            """() => {
                const node = document.querySelector('#storage-backup-output');
                return node && node.textContent.includes('"status": "created"');
            }""",
            timeout=20000,
        )
        backup_result = json.loads(page.locator("#storage-backup-output").inner_text())
        assert backup_result["complete"] is True
        assert backup_result["verified"] is True
        page.wait_for_function(
            "() => document.querySelectorAll('#storage-backups-list button').length > 0",
            timeout=10000,
        )
        page.get_by_role("button", name="بازیابی آزمایشی در پوشهٔ جدا").first.click()
        page.wait_for_function(
            """() => {
                const node = document.querySelector('#storage-backup-output');
                return node && node.textContent.includes('"status": "staged"');
            }""",
            timeout=20000,
        )
        restore_result = json.loads(page.locator("#storage-backup-output").inner_text())
        assert restore_result["status"] == "staged"
        assert restore_result["restore_id"].startswith("restore-")
        assert set(restore_result["database_roles"]) == {
            "settings", "learning", "cloud_resources"
        }

        recommendations_response = page.request.get(
            base_url.rstrip("/") + "/api/learning/recommendations?limit=20"
        )
        assert recommendations_response.status == 200
        recommendations = recommendations_response.json()
        assert isinstance(recommendations.get("recommendations"), list)
        assert recommendations.get("autonomous_action_taken") is False

        page.locator("#refresh-learning-recommendations").click()
        page.wait_for_function(
            """() => {
                const node = document.querySelector('#learning-recommendations-output');
                return node && node.textContent.trim().length > 0 &&
                    !node.textContent.includes('در حال محاسبه');
            }""",
            timeout=15000,
        )
        assert not browser_errors, "Uncaught browser errors: " + "; ".join(browser_errors)
        browser.close()



def test_experience_creation_stays_unverified_and_auto_capture_defaults_off():
    base_url = os.environ.get("E2E_BASE_URL")
    if not base_url:
        pytest.skip("Set E2E_BASE_URL to run browser smoke tests.")
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page()
        page.goto(base_url, wait_until="domcontentloaded")

        consent_checkbox = page.locator("#experience-auto-capture-confirm")
        assert not consent_checkbox.is_checked()
        page.locator("#experience-auto-capture-refresh").click()
        page.wait_for_function(
            """() => {
                const node = document.querySelector('#experience-auto-capture-status');
                return node && node.textContent.includes('ذخیرهٔ خودکار غیرفعال است');
            }""",
            timeout=10000,
        )

        topic = "Browser E2E experience creation"
        page.locator("#experience-topic").fill(topic)
        page.locator("#experience-hypothesis").fill("A local UI submission should create a draft record.")
        page.locator("#experience-problem").fill("Verify that a user-created experience is visible and not verified automatically.")
        page.locator("#experience-create-form button[type=submit]").click()
        page.wait_for_function(
            """() => {
                const node = document.querySelector('#experience-output');
                const list = document.querySelector('#experience-list');
                return node && node.textContent.includes('تعداد تجربه‌ها') &&
                    list && list.textContent.includes('Browser E2E experience creation');
            }""",
            timeout=15000,
        )
        assert "وضعیت: identified" in page.locator("#experience-list").inner_text()
        assert not consent_checkbox.is_checked()

        # Opt-in must be an explicit user action; revocation must return the UI
        # and persisted state to disabled without deleting prior experience rows.
        consent_checkbox.check()
        page.locator("#experience-auto-capture-enable").click()
        page.wait_for_function(
            """() => {
                const node = document.querySelector('#experience-auto-capture-status');
                return node && node.textContent.includes('ذخیرهٔ خودکار فعال است');
            }""",
            timeout=10000,
        )
        assert consent_checkbox.is_checked()

        page.locator("#experience-auto-capture-revoke").click()
        page.wait_for_function(
            """() => {
                const node = document.querySelector('#experience-auto-capture-status');
                return node && node.textContent.includes('ذخیرهٔ خودکار غیرفعال است');
            }""",
            timeout=10000,
        )
        assert not consent_checkbox.is_checked()
        assert "Browser E2E experience creation" in page.locator("#experience-list").inner_text()

        # Revocation must survive a fresh dashboard load, not merely update the
        # current DOM. Existing experience records must remain available.
        page.reload(wait_until="domcontentloaded")
        page.locator("#experience-auto-capture-refresh").click()
        page.wait_for_function(
            """() => {
                const node = document.querySelector('#experience-auto-capture-status');
                return node && node.textContent.includes('ذخیرهٔ خودکار غیرفعال است');
            }""",
            timeout=10000,
        )
        assert not page.locator("#experience-auto-capture-confirm").is_checked()
        page.locator("#experience-refresh").click()
        page.wait_for_function(
            """() => {
                const list = document.querySelector('#experience-list');
                return list && list.textContent.includes('Browser E2E experience creation');
            }""",
            timeout=15000,
        )
        assert "Browser E2E experience creation" in page.locator("#experience-list").inner_text()
        browser.close()



def test_browser_cloud_discovery_displays_query_progress_until_task_finishes():
    base_url = os.environ.get("E2E_BASE_URL")
    if not base_url:
        pytest.skip("Set E2E_BASE_URL to run browser smoke tests.")

    task_reads = {"count": 0}
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page()

        def mock_api(route):
            url = route.request.url
            if url.endswith("/api/cloud-resources/discover"):
                route.fulfill(
                    status=202,
                    content_type="application/json",
                    body=json.dumps({
                        "task": {"id": 987654, "status": "queued"},
                        "message": "queued",
                    }),
                )
                return
            if "/api/tasks/987654" in url:
                task_reads["count"] += 1
                if task_reads["count"] <= 2:
                    task = {
                        "id": 987654,
                        "status": "running",
                        "checkpoint": {
                            "phase": "cloud_discovery_query",
                            "query_index": 2,
                        },
                        "updated_at": "2026-10-19T13:00:00+00:00",
                        "error": None,
                        "result": None,
                    }
                else:
                    task = {
                        "id": 987654,
                        "status": "succeeded",
                        "checkpoint": {
                            "phase": "cloud_discovery_query",
                            "query_index": 4,
                        },
                        "updated_at": "2026-10-19T13:01:00+00:00",
                        "error": None,
                        "result": {
                            "status": "completed",
                            "sources_seen": 7,
                            "candidates_upserted": 2,
                        },
                    }
                route.fulfill(
                    status=200, content_type="application/json",
                    body=json.dumps(task),
                )
                return
            if url.startswith(base_url.rstrip("/") + "/api/cloud-resources?limit="):
                route.fulfill(
                    status=200,
                    content_type="application/json",
                    body=json.dumps({
                        "latest_run": {
                            "status": "completed",
                            "finished_at": "2026-10-19T13:01:00+00:00",
                            "sources_seen": 7,
                            "candidates_upserted": 2,
                            "query_results": [],
                        },
                        "candidates": [],
                    }),
                )
                return
            route.continue_()

        page.route("**/api/**", mock_api)
        page.goto(base_url, wait_until="domcontentloaded")
        button = page.locator("#cloud-discover")
        button.click()

        page.get_by_text("پرس‌وجوی 3 از 5", exact=False).wait_for(timeout=5000)
        assert button.is_disabled()
        page.get_by_text("پایان وظیفهٔ #987654 — succeeded", exact=False).wait_for(
            timeout=10000
        )
        assert not button.is_disabled()
        assert "نتیجهٔ وظیفه: completed" in page.locator("#cloud-output").inner_text()
        browser.close()


def test_browser_can_cancel_a_persisted_scheduled_task():
    base_url = os.environ.get("E2E_BASE_URL")
    if not base_url:
        pytest.skip("Set E2E_BASE_URL to run browser smoke tests.")
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page()
        page.goto(base_url, wait_until="domcontentloaded")

        scheduled_at = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        response = page.request.post(
            base_url.rstrip("/") + "/api/tasks",
            data={
                "task_type": "e2e.scheduled-cancellation",
                "payload": {"case": "browser-task-controls"},
                "priority": 3,
                "max_attempts": 1,
                "scheduled_at": scheduled_at,
            },
        )
        assert response.status == 202
        created = response.json()
        task_id = created["id"]
        assert created["status"] == "queued"

        page.locator("#refresh-tasks").click()
        card = page.locator("#tasks-container article").filter(
            has_text=f"#{task_id} — e2e.scheduled-cancellation"
        )
        card.wait_for(state="visible", timeout=10000)
        card.get_by_role("button", name="لغو").click()
        page.wait_for_function(
            """async (taskId) => {
                const response = await fetch('/api/tasks/' + taskId);
                if (!response.ok) return false;
                const task = await response.json();
                return task.status === 'cancelled';
            }""",
            arg=task_id,
            timeout=10000,
        )
        page.wait_for_function(
            """(taskId) => {
                const card = Array.from(document.querySelectorAll('#tasks-container article'))
                    .find(node => node.textContent.includes('#' + taskId + ' — e2e.scheduled-cancellation'));
                return card && card.textContent.includes('وضعیت: لغوشده');
            }""",
            arg=task_id,
            timeout=10000,
        )
        browser.close()



def test_browser_task_audit_history_loads_older_cursor_pages():
    base_url = os.environ.get("E2E_BASE_URL")
    if not base_url:
        pytest.skip("Set E2E_BASE_URL to run browser smoke tests.")

    task = {
        "id": 424242,
        "task_type": "e2e.audit-history",
        "status": "paused",
        "priority": 3,
        "attempts": 1,
        "max_attempts": 3,
        "created_at": "2026-10-19T12:00:00+00:00",
        "updated_at": "2026-10-19T12:01:00+00:00",
        "error": None,
        "result": None,
    }
    event_requests = []

    def event_page(start, end):
        return [
            {
                "id": event_id,
                "task_id": 424242,
                "event_type": "status_changed",
                "old_status": "queued",
                "new_status": "paused",
                "old_priority": 3,
                "new_priority": 3,
                "old_scheduled_at": None,
                "new_scheduled_at": None,
                "occurred_at": "2026-10-19T12:00:00+00:00",
            }
            for event_id in range(start, end, -1)
        ]

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page()

        def mock_api(route):
            url = route.request.url
            if "/api/tasks/424242/events" in url:
                event_requests.append(url)
                if "before_id=81" in url:
                    payload = {"events": event_page(80, 60), "has_more": False, "next_before_id": None}
                else:
                    payload = {"events": event_page(100, 80), "has_more": True, "next_before_id": 81}
                route.fulfill(status=200, content_type="application/json", body=json.dumps(payload))
                return
            if "/api/tasks?limit=50" in url:
                route.fulfill(
                    status=200,
                    content_type="application/json",
                    body=json.dumps({"tasks": [task], "next_cursor": None, "has_more": False}),
                )
                return
            route.continue_()

        page.route("**/api/**", mock_api)
        page.goto(base_url, wait_until="domcontentloaded")
        page.locator("#refresh-tasks").click()
        card = page.locator("#tasks-container article").filter(has_text="#424242 — e2e.audit-history")
        card.wait_for(timeout=5000)
        card.get_by_role("button", name="نمایش تاریخچه رویدادهای وظیفه 424242").click()
        output = card.locator(".task-events-output")
        output.get_by_text("100", exact=False).wait_for(timeout=5000)
        assert "81" in output.inner_text()
        card.get_by_role("button", name="بارگذاری رویدادهای قدیمی‌تر وظیفه 424242").click()
        output.get_by_text("61", exact=False).wait_for(timeout=5000)
        assert len(event_requests) == 2
        assert "before_id=81" in event_requests[1]
        assert "100" in output.inner_text() and "61" in output.inner_text()
        assert card.get_by_role("button", name="بارگذاری رویدادهای قدیمی‌تر وظیفه 424242").count() == 0
        browser.close()



def test_browser_task_filter_and_cursor_pagination_preserve_cards():
    base_url = os.environ.get("E2E_BASE_URL")
    if not base_url:
        pytest.skip("Set E2E_BASE_URL to run browser smoke tests.")

    def task(task_id, name, status):
        return {
            "id": task_id,
            "task_type": name,
            "status": status,
            "priority": 0,
            "attempts": 1,
            "max_attempts": 1,
            "created_at": "2026-10-19T12:00:00+00:00",
            "updated_at": "2026-10-19T12:00:00+00:00",
            "error": None,
            "result": None,
        }

    requests = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page()

        def mock_api(route):
            url = route.request.url
            if "/api/tasks?" in url:
                requests.append(url)
                if "statuses=failed" in url:
                    payload = {"tasks": [task(303, "e2e.failed", "failed")], "next_cursor": None, "has_more": False}
                elif "cursor=page-two" in url:
                    payload = {"tasks": [task(202, "e2e.second-page", "queued")], "next_cursor": None, "has_more": False}
                else:
                    payload = {"tasks": [task(101, "e2e.first-page", "queued")], "next_cursor": "page-two", "has_more": True}
                route.fulfill(status=200, content_type="application/json", body=json.dumps(payload))
                return
            route.continue_()

        page.route("**/api/**", mock_api)
        page.goto(base_url, wait_until="domcontentloaded")
        page.locator("#refresh-tasks").click()
        page.get_by_text("#101 — e2e.first-page", exact=True).wait_for(timeout=5000)
        page.locator("#load-more-tasks").click()
        page.get_by_text("#202 — e2e.second-page", exact=True).wait_for(timeout=5000)
        assert page.locator("#tasks-container article").count() == 2
        assert "cursor=page-two" in requests[-1]

        page.locator("#task-status-filter").select_option("failed")
        page.get_by_text("#303 — e2e.failed", exact=True).wait_for(timeout=5000)
        assert page.locator("#tasks-container article").count() == 1
        assert page.get_by_text("#101 — e2e.first-page", exact=True).count() == 0
        assert "statuses=failed" in requests[-1]
        browser.close()




def test_browser_task_search_and_live_summary_are_bounded_to_loaded_tasks():
    base_url = os.environ.get("E2E_BASE_URL")
    if not base_url:
        pytest.skip("Set E2E_BASE_URL to run browser smoke tests.")

    tasks = [
        {"id": 6001, "task_type": "e2e.research", "status": "queued", "priority": 1,
         "attempts": 1, "max_attempts": 2, "created_at": "2026-10-20T12:00:00+00:00",
         "updated_at": "2026-10-20T12:00:00+00:00", "error": None, "result": None},
        {"id": 6002, "task_type": "e2e.learning", "status": "failed", "priority": 0,
         "attempts": 2, "max_attempts": 2, "created_at": "2026-10-20T12:01:00+00:00",
         "updated_at": "2026-10-20T12:01:00+00:00", "error": "redacted test error", "result": None},
        {"id": 6003, "task_type": "e2e.backup", "status": "succeeded", "priority": 0,
         "attempts": 1, "max_attempts": 1, "created_at": "2026-10-20T12:02:00+00:00",
         "updated_at": "2026-10-20T12:02:00+00:00", "error": None, "result": None},
    ]

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page()

        def mock_api(route):
            if route.request.method == "GET" and "/api/tasks?" in route.request.url:
                route.fulfill(
                    status=200,
                    content_type="application/json",
                    body=json.dumps({"tasks": tasks, "next_cursor": None, "has_more": False}),
                )
                return
            route.continue_()

        page.route("**/api/**", mock_api)
        page.goto(base_url, wait_until="domcontentloaded")
        page.locator("#refresh-tasks").click()
        cards = page.locator("#tasks-container article")
        page.get_by_text("#6001 — e2e.research", exact=True).wait_for(timeout=5000)
        assert cards.count() == 3
        queued_status = cards.nth(0).locator(".task-status-line")
        failed_status = cards.nth(1).locator(".task-status-line")
        succeeded_status = cards.nth(2).locator(".task-status-line")
        assert "is-queued" in (queued_status.get_attribute("class") or "")
        assert "is-failed" in (failed_status.get_attribute("class") or "")
        assert "is-succeeded" in (succeeded_status.get_attribute("class") or "")
        assert queued_status.get_attribute("data-status") == "queued"
        assert failed_status.get_attribute("data-status") == "failed"
        assert succeeded_status.get_attribute("data-status") == "succeeded"
        assert "وضعیت: در صف" in queued_status.inner_text()
        assert "وضعیت: ناموفق" in failed_status.inner_text()
        assert "وضعیت: موفق" in succeeded_status.inner_text()
        assert queued_status.get_attribute("aria-label") == "وضعیت وظیفه 6001: در صف"
        summary = page.locator("#task-summary")
        assert "بارگذاری‌شده" in summary.inner_text()
        assert "3" in summary.inner_text()
        assert "ناموفق" in summary.inner_text()

        search = page.get_by_role("searchbox", name="جست‌وجو در وظایف بارگذاری‌شده")
        search.fill("6002")
        assert cards.count() == 3
        assert cards.nth(0).is_hidden()
        assert cards.nth(1).is_visible()
        assert cards.nth(2).is_hidden()
        assert "نمایش مطابق جست‌وجو" in summary.inner_text()
        assert "1" in summary.inner_text()

        search.fill("not-present")
        assert cards.nth(0).is_hidden()
        assert cards.nth(1).is_hidden()
        assert cards.nth(2).is_hidden()
        empty_state = page.locator(".task-empty-state")
        assert empty_state.is_visible()
        assert "هیچ وظیفه‌ای با فیلترهای فعلی پیدا نشد" in empty_state.inner_text()
        empty_state.get_by_role("button", name="پاک کردن فیلترها").click()
        page.wait_for_function("document.querySelector('#task-search').value === ''")
        page.wait_for_function("Array.from(document.querySelectorAll('#tasks-container article')).some(card => !card.hidden)")
        search.fill("e2e.backup")
        assert cards.nth(2).is_visible()
        assert cards.nth(1).is_hidden()
        search.fill("")
        tasks.clear()
        page.locator("#refresh-tasks").click()
        page.get_by_text("هنوز وظیفه‌ای ثبت نشده است.", exact=True).wait_for(timeout=5000)
        assert page.locator(".task-empty-state").is_visible()
        browser.close()





def test_browser_task_diagnostics_are_collapsed_and_accessibly_disclosed():
    base_url = os.environ.get("E2E_BASE_URL")
    if not base_url:
        pytest.skip("Set E2E_BASE_URL to run browser smoke tests.")

    task = {
        "id": 6201,
        "task_type": "e2e.diagnostic-disclosure",
        "status": "failed",
        "priority": 0,
        "attempts": 1,
        "max_attempts": 2,
        "created_at": "2026-10-20T08:00:00+00:00",
        "updated_at": "2026-10-20T08:01:00+00:00",
        "error": "redacted diagnostic failure",
        "result": {"phase": "validation", "retryable": True},
    }

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page()

        def mock_api(route):
            if route.request.method == "GET" and "/api/tasks?" in route.request.url:
                route.fulfill(
                    status=200,
                    content_type="application/json",
                    body=json.dumps({"tasks": [task], "next_cursor": None, "has_more": False}),
                )
                return
            route.continue_()

        page.route("**/api/**", mock_api)
        page.goto(base_url, wait_until="domcontentloaded")
        page.locator("#refresh-tasks").click()
        card = page.locator("#tasks-container article").filter(
            has_text="#6201 — e2e.diagnostic-disclosure"
        )
        details = card.locator("details.task-diagnostics")
        details.wait_for(state="attached", timeout=10000)
        assert details.get_attribute("open") is None
        summary = details.locator("summary")
        assert summary.inner_text() == "جزئیات خطا و نتیجه"
        summary.click()
        assert details.get_attribute("open") is not None
        assert details.get_by_text("redacted diagnostic failure").is_visible()
        assert '"phase": "validation"' in details.locator(".task-result-detail").inner_text()
        assert details.get_by_role("heading", name="خطا").count() == 1
        assert details.get_by_role("heading", name="نتیجه").count() == 1
        browser.close()


def test_browser_task_bulk_controls_only_submit_actions_for_eligible_selected_tasks():
    base_url = os.environ.get("E2E_BASE_URL")
    if not base_url:
        pytest.skip("Set E2E_BASE_URL to run browser smoke tests.")

    tasks = [
        {"id": 6101, "task_type": "e2e.queued", "status": "queued", "priority": 1,
         "attempts": 1, "max_attempts": 2, "created_at": "2026-10-20T12:00:00+00:00",
         "updated_at": "2026-10-20T12:00:00+00:00", "error": None, "result": None},
        {"id": 6102, "task_type": "e2e.paused", "status": "paused", "priority": 0,
         "attempts": 1, "max_attempts": 2, "created_at": "2026-10-20T12:01:00+00:00",
         "updated_at": "2026-10-20T12:01:00+00:00", "error": None, "result": None},
        {"id": 6103, "task_type": "e2e.failed", "status": "failed", "priority": 0,
         "attempts": 2, "max_attempts": 2, "created_at": "2026-10-20T12:02:00+00:00",
         "updated_at": "2026-10-20T12:02:00+00:00", "error": "redacted test error", "result": None},
    ]
    requests = []

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page()

        def mock_api(route):
            request = route.request
            if request.method == "GET" and "/api/tasks?" in request.url:
                route.fulfill(
                    status=200,
                    content_type="application/json",
                    body=json.dumps({"tasks": tasks, "next_cursor": None, "has_more": False}),
                )
                return
            if request.method == "POST" and "/api/tasks/" in request.url:
                requests.append(request.url)
                route.fulfill(status=200, content_type="application/json", body=json.dumps({"ok": True}))
                return
            route.continue_()

        page.route("**/api/**", mock_api)
        page.goto(base_url, wait_until="domcontentloaded")
        page.locator("#refresh-tasks").click()
        page.get_by_text("#6101 — e2e.queued", exact=True).wait_for(timeout=5000)
        page.locator("#task-select-all").check()
        assert page.locator("#task-selected-count").inner_text().startswith("انتخاب‌شده:")
        assert page.locator("#bulk-pause-tasks").is_enabled()
        assert page.locator("#bulk-resume-tasks").is_enabled()
        assert page.locator("#bulk-cancel-tasks").is_enabled()

        page.locator("#bulk-pause-tasks").click()
        page.get_by_text("توقف گروهی پایان یافت", exact=False).wait_for(timeout=5000)
        assert len(requests) == 1
        assert requests[0].endswith("/api/tasks/6101/pause")
        assert not any("/6102/pause" in url or "/6103/pause" in url for url in requests)
        browser.close()


def test_browser_task_priority_and_schedule_controls_submit_validated_updates():
    base_url = os.environ.get("E2E_BASE_URL")
    if not base_url:
        pytest.skip("Set E2E_BASE_URL to run browser smoke tests.")

    requests = []
    task = {
        "id": 5151,
        "task_type": "e2e.manage-task",
        "status": "queued",
        "priority": 2,
        "attempts": 1,
        "max_attempts": 3,
        "scheduled_at": "2030-01-01T12:00:00+00:00",
        "created_at": "2026-10-20T12:00:00+00:00",
        "updated_at": "2026-10-20T12:00:00+00:00",
        "error": None,
        "result": None,
    }

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page()

        def mock_api(route):
            request = route.request
            url = request.url
            if request.method == "GET" and "/api/tasks?" in url:
                route.fulfill(
                    status=200,
                    content_type="application/json",
                    body=json.dumps({"tasks": [task], "next_cursor": None, "has_more": False}),
                )
                return
            if request.method == "PATCH" and url.endswith("/priority"):
                body = request.post_data_json
                requests.append(("priority", body))
                task["priority"] = body["priority"]
                route.fulfill(status=200, content_type="application/json", body=json.dumps(task))
                return
            if request.method == "PATCH" and url.endswith("/schedule"):
                body = request.post_data_json
                requests.append(("schedule", body))
                task["scheduled_at"] = body["scheduled_at"]
                route.fulfill(status=200, content_type="application/json", body=json.dumps(task))
                return
            route.continue_()

        page.route("**/api/**", mock_api)
        page.goto(base_url, wait_until="domcontentloaded")
        page.locator("#refresh-tasks").click()
        card = page.locator("#tasks-container article").filter(has_text="#5151 — e2e.manage-task")
        card.wait_for(timeout=5000)
        card.get_by_role("spinbutton", name="اولویت وظیفه 5151").fill("17")
        card.get_by_role("button", name="ذخیره اولویت وظیفه 5151").click()
        page.wait_for_function("document.querySelector('#tasks-output').textContent.includes('ذخیره شد')")
        card = page.locator("#tasks-container article").filter(has_text="#5151 — e2e.manage-task")
        card.locator('input[type="datetime-local"][aria-label="زمان اجرای وظیفه 5151"]').fill("2030-01-02T12:30")
        card.get_by_role("button", name="ذخیره زمان اجرای وظیفه 5151").click()
        page.wait_for_function("document.querySelector('#tasks-output').textContent.includes('ذخیره شد')")
        assert requests[0] == ("priority", {"priority": 17})
        assert requests[1][0] == "schedule"
        assert requests[1][1]["scheduled_at"].startswith("2030-01-02T")
        browser.close()


def test_browser_task_priority_and_schedule_controls_hidden_for_running_task():
    base_url = os.environ.get("E2E_BASE_URL")
    if not base_url:
        pytest.skip("Set E2E_BASE_URL to run browser smoke tests.")

    task = {
        "id": 5152,
        "task_type": "e2e.running-task",
        "status": "running",
        "priority": 2,
        "attempts": 1,
        "max_attempts": 1,
        "created_at": "2026-10-20T12:00:00+00:00",
        "updated_at": "2026-10-20T12:00:00+00:00",
        "error": None,
        "result": None,
    }
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page()

        def mock_api(route):
            if route.request.method == "GET" and "/api/tasks?" in route.request.url:
                route.fulfill(
                    status=200,
                    content_type="application/json",
                    body=json.dumps({"tasks": [task], "next_cursor": None, "has_more": False}),
                )
                return
            route.continue_()

        page.route("**/api/**", mock_api)
        page.goto(base_url, wait_until="domcontentloaded")
        page.locator("#refresh-tasks").click()
        card = page.locator("#tasks-container article").filter(has_text="#5152 — e2e.running-task")
        card.wait_for(timeout=5000)
        assert card.get_by_role("button", name="ذخیره اولویت وظیفه 5152").count() == 0
        assert card.get_by_role("button", name="ذخیره زمان اجرای وظیفه 5152").count() == 0
        browser.close()


def test_browser_self_improvement_proposal_is_saved_without_mutating_checkout():
    base_url = os.environ.get("E2E_BASE_URL")
    if not base_url:
        pytest.skip("Set E2E_BASE_URL to run browser smoke tests.")
    development_doc = Path(__file__).resolve().parents[1] / "docs" / "DEVELOPMENT.md"
    original_content = development_doc.read_text(encoding="utf-8")
    title = "Browser E2E proposal safety boundary"
    patch = (
        "diff --git a/docs/DEVELOPMENT.md b/docs/DEVELOPMENT.md\n"
        "--- a/docs/DEVELOPMENT.md\n"
        "+++ b/docs/DEVELOPMENT.md\n"
        "@@ -1 +1 @@\n"
        "-# راه‌اندازی محیط توسعه\n"
        "+# راه‌اندازی محیط توسعه (پیشنهاد آزمایشی)\n"
    )
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page()
        page.goto(base_url, wait_until="domcontentloaded")
        page.locator("#self-improvement-title").fill(title)
        page.locator("#self-improvement-rationale").fill(
            "Verify the local UI records a proposed patch without applying it to the checkout."
        )
        page.locator("#self-improvement-patch").fill(patch)
        with page.expect_response(
            lambda response: response.url.endswith("/api/self-improvement/proposals")
            and response.request.method == "POST"
        ) as response_info:
            page.locator("#self-improvement-form button[type=submit]").click()
        create_response = response_info.value
        assert create_response.status == 201, create_response.text()
        page.wait_for_function(
            """() => {
                const output = document.querySelector('#self-improvement-output');
                return output && output.textContent.includes('هنوز هیچ تغییری روی فایل‌های پروژه اعمال نشده است');
            }""",
            timeout=10000,
        )
        response = page.request.get(base_url.rstrip("/") + "/api/self-improvement/proposals")
        assert response.status == 200
        proposals = response.json()["proposals"]
        proposal = next(item for item in proposals if item["title"] == title)
        assert proposal["status"] == "proposed"
        assert proposal["approved_at"] is None
        assert development_doc.read_text(encoding="utf-8") == original_content
        assert title in page.locator("#self-improvement-list").inner_text()
        browser.close()


def test_browser_restores_saved_semantic_review_and_only_generates_after_confirmation():
    base_url = os.environ.get("E2E_BASE_URL")
    if not base_url:
        pytest.skip("Set E2E_BASE_URL to run browser smoke tests.")
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page()
        page.goto(base_url, wait_until="domcontentloaded")

        saved_review = {
            "kind": "model_assisted_advisory",
            "review": {
                "items": [{
                    "index": 1,
                    "judgment": "matches",
                    "rationale": "Saved semantic review rationale.",
                    "missing_concepts": [],
                }],
                "overall_note": "Saved advisory note.",
            },
            "note": "This review is advisory only.",
            "score_changed": False,
            "independent_verification": False,
        }
        saved_calls = {"get": 0, "post": 0}

        def saved_route(route):
            request = route.request
            if request.method == "GET":
                saved_calls["get"] += 1
                route.fulfill(status=200, content_type="application/json", body=json.dumps({
                    "job_id": 990001,
                    "attempt_id": 11,
                    "semantic_review": saved_review,
                }))
            else:
                saved_calls["post"] += 1
                route.fulfill(status=500, content_type="application/json", body='{"detail":"unexpected POST"}')

        page.route("**/api/learning/jobs/990001/assessment/11/semantic-review", saved_route)
        page.evaluate("""() => {
            const output = document.createElement('div');
            output.id = 'semantic-review-saved-output';
            document.body.append(output);
            renderLessonResult(output, {
                id: 990001, status: 'completed', lesson: 'A saved lesson.',
                quality: {score: 90},
                assessment: {questions: ['Question?'], answer_key: ['Answer']},
                assessment_attempts: [{id: 11, score: 0, correct_count: 0, question_count: 1}],
                latest_review: null
            }, {id: 1}, {id: 2, row: null});
        }""")
        saved_output = page.locator("#semantic-review-saved-output")
        saved_output.get_by_role("button", name="مشاهده/اجرای بازبینی معنایی کمکی").click()
        page.get_by_text("Saved semantic review rationale.").wait_for(timeout=5000)
        assert "Saved advisory note." in saved_output.inner_text()
        assert saved_calls == {"get": 1, "post": 0}

        new_calls = {"get": 0, "post": 0, "confirm": None}

        def new_route(route):
            request = route.request
            if request.method == "GET":
                new_calls["get"] += 1
                route.fulfill(
                    status=404,
                    content_type="application/json",
                    body='{"detail":"No semantic review is stored for this attempt."}',
                )
                return
            new_calls["post"] += 1
            new_calls["confirm"] = request.post_data_json
            route.fulfill(status=200, content_type="application/json", body=json.dumps({
                "kind": "model_assisted_advisory",
                "review": {
                    "items": [{
                        "index": 1,
                        "judgment": "partial",
                        "rationale": "Newly generated advisory rationale.",
                        "missing_concepts": ["trade-off"],
                    }],
                    "overall_note": "New advisory note.",
                },
                "note": "This review is advisory only.",
                "score_changed": False,
                "independent_verification": False,
            }))

        page.route("**/api/learning/jobs/990002/assessment/22/semantic-review", new_route)
        page.on("dialog", lambda dialog: dialog.accept())
        page.evaluate("""() => {
            const output = document.createElement('div');
            output.id = 'semantic-review-new-output';
            document.body.append(output);
            renderLessonResult(output, {
                id: 990002, status: 'completed', lesson: 'Another lesson.',
                quality: {score: 90},
                assessment: {questions: ['Question?'], answer_key: ['Answer']},
                assessment_attempts: [{id: 22, score: 0, correct_count: 0, question_count: 1}],
                latest_review: null
            }, {id: 1}, {id: 3, row: null});
        }""")
        new_output = page.locator("#semantic-review-new-output")
        new_output.get_by_role("button", name="مشاهده/اجرای بازبینی معنایی کمکی").click()
        page.get_by_text("Newly generated advisory rationale.").wait_for(timeout=5000)
        assert "New advisory note." in new_output.inner_text()
        assert new_calls == {"get": 1, "post": 1, "confirm": {"confirm": True}}
        browser.close()

def test_browser_reloads_and_recovers_persisted_research_task():
    base_url = os.environ.get("E2E_BASE_URL")
    if not base_url:
        pytest.skip("Set E2E_BASE_URL to run browser smoke tests.")
    private_message = "E2E private research reload marker 7d91a2"
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page()
        page.goto(base_url, wait_until="domcontentloaded")

        response = page.request.post(
            base_url.rstrip("/") + "/api/research/tasks",
            data={"message": private_message},
        )
        assert response.status == 202, response.text()
        task = response.json()["task"]
        task_id = task["id"]
        assert task["task_type"] == "research.answer"

        # The durable task list, not transient chat-page state, must restore the
        # task after navigation. The user prompt itself must remain hidden.
        page.reload(wait_until="domcontentloaded")
        page.locator("#refresh-tasks").click()
        card = page.locator("#tasks-container article").filter(
            has_text=f"#{task_id} — research.answer"
        )
        card.wait_for(state="visible", timeout=10000)
        assert "وضعیت:" in card.inner_text()
        assert private_message not in card.inner_text()
        browser.close()

def test_browser_validates_and_rejects_self_improvement_proposal_without_applying_patch():
    base_url = os.environ.get("E2E_BASE_URL")
    if not base_url:
        pytest.skip("Set E2E_BASE_URL to run browser smoke tests.")
    proposed_doc = Path(__file__).resolve().parents[1] / "docs" / "E2E-REJECTED-PROPOSAL.md"
    assert not proposed_doc.exists()
    title = "Browser E2E proposal validation and rejection"
    patch = (
        "diff --git a/docs/E2E-REJECTED-PROPOSAL.md b/docs/E2E-REJECTED-PROPOSAL.md\n"
        "new file mode 100644\n"
        "--- /dev/null\n"
        "+++ b/docs/E2E-REJECTED-PROPOSAL.md\n"
        "@@ -0,0 +1 @@\n"
        "+# Temporary proposal that must never be applied\n"
    )
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page()
        page.goto(base_url, wait_until="domcontentloaded")
        page.on("dialog", lambda dialog: dialog.accept())
        page.locator("#self-improvement-title").fill(title)
        page.locator("#self-improvement-rationale").fill(
            "Exercise proposal validation and rejection without changing the checkout."
        )
        page.locator("#self-improvement-patch").fill(patch)
        with page.expect_response(
            lambda response: response.url.endswith("/api/self-improvement/proposals")
            and response.request.method == "POST"
        ) as response_info:
            page.locator("#self-improvement-form button[type=submit]").click()
        assert response_info.value.status == 201, response_info.value.text()

        card = page.locator("#self-improvement-list article").filter(has_text=title)
        card.wait_for(state="visible", timeout=10000)
        card.get_by_role("button", name="بررسی سازگاری diff").click()
        page.wait_for_function(
            """(proposalTitle) => {
                const card = Array.from(document.querySelectorAll('#self-improvement-list article'))
                    .find(node => node.textContent.includes(proposalTitle));
                return card && card.textContent.includes('(validated)');
            }""",
            arg=title,
            timeout=10000,
        )

        card = page.locator("#self-improvement-list article").filter(has_text=title)
        card.get_by_role("button", name="رد پیشنهاد").click()
        page.wait_for_function(
            """(proposalTitle) => {
                const card = Array.from(document.querySelectorAll('#self-improvement-list article'))
                    .find(node => node.textContent.includes(proposalTitle));
                return card && card.textContent.includes('(rejected)');
            }""",
            arg=title,
            timeout=10000,
        )
        response = page.request.get(base_url.rstrip("/") + "/api/self-improvement/proposals")
        assert response.status == 200
        proposals = response.json()["proposals"]
        proposal = next(item for item in proposals if item["title"] == title)
        assert proposal["status"] == "rejected"
        assert proposal["approved_at"] is None
        assert not proposed_doc.exists()
        browser.close()

def test_browser_requires_confirmation_for_draft_pr_and_rollback_requests():
    base_url = os.environ.get("E2E_BASE_URL")
    if not base_url:
        pytest.skip("Set E2E_BASE_URL to run browser smoke tests.")

    proposal_id = 990123
    proposal = {
        "id": proposal_id,
        "title": "Browser E2E draft PR controls",
        "rationale": "Verify confirmation and draft-only behavior.",
        "patch": "diff --git a/docs/example.md b/docs/example.md",
        "status": "approved_for_pr",
        "approved_at": "2026-10-17T00:00:00Z",
        "static_check": {"status": "passed"},
    }
    calls = {"open_pr": [], "rollback_pr": []}
    dialogs = {"next_action": "dismiss"}

    def api_route(route):
        request = route.request
        if request.method == "GET":
            route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps({"proposals": [proposal]}),
            )
            return
        path = request.url.split("?", 1)[0]
        if path.endswith(f"/{proposal_id}/open-pr"):
            calls["open_pr"].append(request.post_data_json)
            route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps({"pull_request": {
                    "pull_request_url": "https://github.com/example/repo/pull/123",
                    "branch": "self-improvement/example",
                    "draft": True,
                    "merged": False,
                }}),
            )
            return
        if path.endswith(f"/{proposal_id}/rollback-pr"):
            calls["rollback_pr"].append(request.post_data_json)
            route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps({"rollback_pull_request": {
                    "pull_request_url": "https://github.com/example/repo/pull/124",
                    "draft": True,
                    "merged": False,
                }}),
            )
            return
        route.fulfill(status=404, content_type="application/json", body='{"detail":"unexpected request"}')

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page()
        page.route("**/api/self-improvement/proposals**", api_route)

        def handle_dialog(dialog):
            if dialogs["next_action"] == "dismiss":
                dialogs["next_action"] = "accept"
                dialog.dismiss()
            else:
                dialog.accept()

        page.on("dialog", handle_dialog)
        page.goto(base_url, wait_until="domcontentloaded")
        page.locator("#self-improvement-refresh").click()

        card = page.locator("#self-improvement-list article").filter(
            has_text=proposal["title"]
        )
        open_button = card.get_by_role("button", name="ایجاد PR پیش‌نویس در GitHub")
        open_button.wait_for(state="visible", timeout=10000)
        open_button.click()
        assert calls["open_pr"] == []

        open_button.click()
        page.get_by_text("https://github.com/example/repo/pull/123").wait_for(timeout=10000)
        assert calls["open_pr"] == [{"confirm": True}]
        assert "ادغام یا استقرار انجام نشده است" in page.locator("#self-improvement-output").inner_text()

        rollback_button = card.get_by_role("button", name="ایجاد PR پیش‌نویس بازگشت")
        rollback_button.click()
        page.get_by_text("https://github.com/example/repo/pull/124").wait_for(timeout=10000)
        assert calls["rollback_pr"] == [{"confirm": True}]
        assert "ادغام یا استقرار انجام نشده است" in page.locator("#self-improvement-output").inner_text()
        browser.close()

def test_browser_can_review_and_persist_an_unverified_experience():
    base_url = os.environ.get("E2E_BASE_URL")
    if not base_url:
        pytest.skip("Set E2E_BASE_URL to run browser smoke tests.")
    topic = "Browser E2E experience review persistence"
    takeaway = "Review changes persist while verification remains explicit."
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page()
        page.goto(base_url, wait_until="domcontentloaded")

        page.locator("#experience-topic").fill(topic)
        page.locator("#experience-hypothesis").fill(
            "A review submitted in the browser should persist to the local store."
        )
        page.locator("#experience-problem").fill(
            "Ensure review controls update a draft without silently verifying it."
        )
        page.locator("#experience-create-form button[type=submit]").click()
        page.get_by_text(topic, exact=True).wait_for(timeout=15000)

        card = page.locator("#experience-list article").filter(has_text=topic)
        card.wait_for(state="visible", timeout=10000)
        assert "وضعیت: identified" in card.inner_text()
        review_form = card.locator("form").last
        review_form.locator("select").select_option("researching")
        review_form.locator("textarea").nth(2).fill(takeaway)

        with page.expect_response(
            lambda response: "/api/experiences/" in response.url
            and response.request.method == "PATCH"
        ) as response_info:
            review_form.get_by_role("button", name="ذخیرهٔ بازبینی").click()
        assert response_info.value.ok, response_info.value.text()

        card = page.locator("#experience-list article").filter(has_text=topic)
        page.wait_for_function(
            """({topic, takeaway}) => {
                const cards = Array.from(document.querySelectorAll('#experience-list article'));
                const card = cards.find(node => node.textContent.includes(topic));
                return !!card && card.textContent.includes('وضعیت: researching') &&
                    card.textContent.includes(takeaway);
            }""",
            arg={"topic": topic, "takeaway": takeaway},
            timeout=15000,
        )
        response = page.request.get(base_url.rstrip("/") + "/api/experiences?limit=100")
        assert response.status == 200
        saved = next(item for item in response.json()["items"] if item["topic"] == topic)
        assert saved["status"] == "researching"
        assert saved["takeaway"] == takeaway
        assert saved.get("verified_at") is None
        browser.close()


def test_dashboard_navigation_and_layout_are_accessible_and_responsive():
    base_url = os.environ.get("E2E_BASE_URL")
    if not base_url:
        pytest.skip("Set E2E_BASE_URL to run browser smoke tests.")
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        response = page.goto(base_url, wait_until="domcontentloaded")
        assert response is not None and response.status == 200
        page.keyboard.press("Tab")
        assert page.evaluate("document.activeElement.className === 'skip-link'")
        assert page.locator(".skip-link").get_attribute("href") == "#main-content"
        navigation = page.get_by_role("navigation", name="ناوبری بخش‌های داشبورد")
        assert navigation.is_visible()
        current_links = navigation.locator('a[aria-current="location"]')
        assert current_links.count() == 1, "Exactly one navigation item should identify the current section"
        assert current_links.get_attribute("href") == "#storage-health-section"
        navigation.get_by_role("link", name="وظایف").click()
        page.wait_for_function(
            "document.querySelector('.dashboard-nav a[aria-current=\\\"location\\\"]')?.getAttribute('href') === '#task-manager-section'",
            timeout=5000,
        )
        shell = page.locator(".dashboard-shell")
        assert shell.count() == 1
        for target in (
            "storage-health-section", "chat-section", "learning-section",
            "task-manager-section", "experience-memory-section",
            "cloud-resources-section", "cloud-plan-section", "kaggle-quota-section",
            "oci-limits-section", "sandbox-section", "self-improvement-section",
            "learning-paths-section", "knowledge-section", "settings-section",
        ):
            assert navigation.locator(f'a[href="#{target}"]').count() == 1
            assert page.locator(f"#{target}").count() == 1
        stylesheet = page.request.get(base_url.rstrip("/") + "/static/dashboard.css")
        assert stylesheet.status == 200
        assert "prefers-reduced-motion" in stylesheet.text()
        assert "focus-visible" in stylesheet.text()
        assert page.locator("html").get_attribute("dir") == "rtl"
        assert page.locator("#storage-health-output").get_attribute("aria-live") == "polite"
        missing_names = page.locator("input[placeholder], textarea[placeholder], select[placeholder]").evaluate_all(
            "nodes => nodes.filter(node => !node.getAttribute('aria-label')).map(node => node.outerHTML)"
        )
        assert missing_names == [], f"Controls missing accessible names: {missing_names}"
        for width in (360, 768, 1280):
            page.set_viewport_size({"width": width, "height": 900})
            nav_direction = navigation.evaluate("node => getComputedStyle(node).flexDirection")
            assert nav_direction == ("column" if width > 1000 else "row")
            dimensions = page.evaluate("() => ({viewport: document.documentElement.clientWidth, document: document.documentElement.scrollWidth})")
            assert dimensions["document"] <= dimensions["viewport"] + 1, f"Page overflow at {width}px: {dimensions}"
        browser.close()


def test_text_fields_adapt_direction_to_language_and_keep_code_ltr():
    base_url = os.environ.get("E2E_BASE_URL")
    if not base_url:
        pytest.skip("Set E2E_BASE_URL to run browser smoke tests.")
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page()
        page.goto(base_url, wait_until="domcontentloaded")
        message = page.locator("#message")
        assert message.get_attribute("dir") == "auto"
        message.fill("How do SQL execution plans work?")
        assert message.evaluate("node => getComputedStyle(node).direction") == "ltr"
        message.fill("چطور برنامه اجرای SQL کار می‌کند؟")
        assert message.evaluate("node => getComputedStyle(node).direction") == "rtl"
        assert page.locator("#sandbox-source").get_attribute("dir") == "ltr"
        assert page.locator("#self-improvement-patch").get_attribute("dir") == "ltr"
        browser.close()



def test_learning_path_progress_is_derived_and_accessible():
    base_url = os.environ.get("E2E_BASE_URL")
    if not base_url:
        pytest.skip("Set E2E_BASE_URL to run browser smoke tests.")

    path = {
        "id": 7101,
        "title": "مسیر آزمایشی یادگیری",
        "progress_percent": 99,
        "completed_count": 1,
        "topic_count": 3,
        "reference_urls": [],
        "topics": [
            {"id": 71011, "position": 1, "topic": "مبانی", "status": "completed", "latest_score": 90},
            {"id": 71012, "position": 2, "topic": "تمرین", "status": "in_progress", "latest_score": None},
            {"id": 71013, "position": 3, "topic": "مرور", "status": "pending", "latest_score": None},
        ],
    }

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page()

        def mock_learning_api(route):
            url = route.request.url
            if route.request.method == "GET" and "/api/learning/paths" in url:
                route.fulfill(status=200, content_type="application/json",
                              body=json.dumps({"paths": [path]}, ensure_ascii=False))
            elif "/api/learning/curricula" in url:
                route.fulfill(status=200, content_type="application/json",
                              body=json.dumps({"curricula": []}, ensure_ascii=False))
            elif "/api/learning/reviews/due" in url:
                route.fulfill(status=200, content_type="application/json",
                              body=json.dumps({"items": []}, ensure_ascii=False))
            elif "/api/learning/recommendations" in url:
                route.fulfill(status=200, content_type="application/json",
                              body=json.dumps({"recommendations": []}, ensure_ascii=False))
            else:
                route.continue_()

        page.route("**/api/learning/**", mock_learning_api)
        page.goto(base_url, wait_until="domcontentloaded")
        progress = page.get_by_role("progressbar", name="پیشرفت مسیر مسیر آزمایشی یادگیری")
        progress.wait_for(state="visible", timeout=10000)
        assert progress.get_attribute("max") == "100"
        assert progress.get_attribute("value") == "33"
        assert progress.get_attribute("aria-valuetext") == "۳۳ درصد تکمیل‌شده"
        assert page.locator(".path-progress-caption").inner_text() == "۱ از ۳ سرفصل تکمیل شده است."
        assert "33٪ (1/3)" in page.locator("#paths-container summary").inner_text()
        browser.close()
