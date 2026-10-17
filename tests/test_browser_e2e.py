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
                return card && card.textContent.includes('وضعیت: cancelled');
            }""",
            arg=task_id,
            timeout=10000,
        )
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
