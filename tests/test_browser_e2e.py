"""Small real-browser smoke tests for the local dashboard.

The regular unit suite skips these unless E2E_BASE_URL is explicitly configured.
"""
import json
import os

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
                    !node.textContent.includes('وضعیت ذخیره‌سازی هنوز بررسی نشده است');
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

        recommendations_response = page.request.get(
            base_url.rstrip("/") + "/api/learning/recommendations?limit=20"
        )
        assert recommendations_response.status == 200
        recommendations = recommendations_response.json()
        assert isinstance(recommendations.get("recommendations"), list)
        assert recommendations.get("autonomous_action_taken") is False

        recommendations_output = page.locator("#learning-recommendations-output")
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
