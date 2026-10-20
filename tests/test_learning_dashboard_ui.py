import json
import os

from playwright.sync_api import sync_playwright


def test_learning_path_progress_is_derived_and_accessible():
    base_url = os.environ.get("E2E_BASE_URL")
    if not base_url:
        import pytest
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
