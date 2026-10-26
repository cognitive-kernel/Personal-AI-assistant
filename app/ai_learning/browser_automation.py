"""Playwright-based visible-UI automation for AI-learning runs.

This module controls a dedicated persistent Chrome profile and only reads/writes
the visible ChatGPT page. It never uses private endpoints or session cookies.
"""
from __future__ import annotations

import os
import threading
import time
from pathlib import Path
from typing import Any

from . import service

_LOCK = threading.Lock()
_ACTIVE = False
_RESPONSE_TIMEOUT_SECONDS = 600
_STABLE_SECONDS = 4.0


class BrowserAutomationError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _chrome_path() -> str | None:
    configured = os.getenv("AI_LEARNING_CHROME_PATH", "").strip()
    candidates = [configured] if configured else []
    if os.name == "nt":
        candidates += [
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        ]
    else:
        candidates += ["/usr/bin/google-chrome", "/usr/bin/chromium", "/usr/bin/chromium-browser"]
    return next((candidate for candidate in candidates if candidate and Path(candidate).is_file()), None)


def _profile_path() -> Path:
    configured = os.getenv("AI_LEARNING_BROWSER_PROFILE", "").strip()
    path = Path(configured) if configured else Path("data/ai_learning_browser_profile")
    if not path.is_absolute():
        path = Path.cwd() / path
    path.mkdir(parents=True, exist_ok=True)
    return path


def _visible_response(page: Any) -> str:
    messages = page.locator('[data-message-author-role="assistant"]')
    count = messages.count()
    if not count:
        return ""
    try:
        return messages.nth(count - 1).inner_text(timeout=3000).strip()
    except Exception:
        return ""


def _send_prompt(page: Any, prompt: str) -> None:
    composer_candidates = [
        "#prompt-textarea",
        'div[contenteditable="true"][data-testid="prompt-textarea"]',
        'div[contenteditable="true"][role="textbox"]',
        'textarea[placeholder]',
    ]
    composer = None
    for selector in composer_candidates:
        locator = page.locator(selector).last
        try:
            if locator.count() and locator.is_visible(timeout=1000):
                composer = locator
                break
        except Exception:
            continue
    if composer is None:
        raise BrowserAutomationError(
            "chatgpt_composer_not_found",
            "کادر نوشتن ChatGPT پیدا نشد؛ صفحه را بررسی کنید و دوباره تلاش کنید.",
        )
    try:
        composer.click(timeout=5000)
        composer.fill(prompt, timeout=10000)
    except Exception as exc:
        raise BrowserAutomationError(
            "chatgpt_composer_unavailable",
            "کادر نوشتن ChatGPT قابل استفاده نیست؛ اگر صفحه ورود یا تأیید انسانی می‌خواهد، آن را در مرورگر تکمیل کنید.",
        ) from exc

    send_candidates = [
        'button[data-testid="send-button"]',
        'button[aria-label="Send prompt"]',
        'button[aria-label="Send message"]',
        'button[title="Send prompt"]',
    ]
    for selector in send_candidates:
        button = page.locator(selector).last
        try:
            if button.count() and button.is_visible(timeout=800) and button.is_enabled():
                button.click(timeout=5000)
                return
        except Exception:
            continue
    # Keyboard submission is only used after the visible composer was positively found.
    try:
        composer.press("Enter", timeout=3000)
    except Exception as exc:
        raise BrowserAutomationError(
            "chatgpt_send_not_found",
            "دکمه ارسال پیدا نشد و ارسال با صفحه‌کلید هم موفق نبود.",
        ) from exc


def _wait_for_complete_answer(page: Any, previous_answer: str) -> str:
    deadline = time.monotonic() + _RESPONSE_TIMEOUT_SECONDS
    last_text = ""
    stable_since: float | None = None
    while time.monotonic() < deadline:
        try:
            current = _visible_response(page)
            stop_visible = False
            for selector in (
                'button[data-testid="stop-button"]',
                'button[aria-label*="Stop generating"]',
                'button[aria-label*="Stop streaming"]',
            ):
                try:
                    if page.locator(selector).last.is_visible(timeout=150):
                        stop_visible = True
                        break
                except Exception:
                    pass
            # Some ChatGPT UI versions leave a stale/hidden stop-generation control
            # visible after the answer has finished. Track text stability independently
            # and use a longer grace period when that control remains visible.
            if current and current != previous_answer and current == last_text:
                if stable_since is None:
                    stable_since = time.monotonic()
                stable_for = time.monotonic() - stable_since
                required_stability = 25.0 if stop_visible else _STABLE_SECONDS
                if stable_for >= required_stability:
                    if len(current) < 20:
                        raise BrowserAutomationError(
                            "chatgpt_response_too_short",
                            "پاسخ ChatGPT بیش از حد کوتاه است و برای ثبت خودکار پذیرفته نشد.",
                        )
                    return current
            else:
                stable_since = None
            last_text = current
        except BrowserAutomationError:
            raise
        except Exception:
            pass
        page.wait_for_timeout(1000)
    raise BrowserAutomationError(
        "chatgpt_response_timeout",
        "در مهلت ۱۰ دقیقه‌ای پاسخ کامل و پایدار از ChatGPT دریافت نشد. اجرای کار برای بررسی در داشبورد نگه داشته شد.",
    )


def _browser_capture(prompt: str) -> str:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise BrowserAutomationError(
            "playwright_not_installed",
            'Playwright نصب نیست. در پوشه پروژه دستور pip install -e ".[browser]" را اجرا کنید.',
        ) from exc

    chrome = _chrome_path()
    if not chrome:
        raise BrowserAutomationError(
            "chrome_not_found",
            "Chrome پیدا نشد. مسیر آن را در AI_LEARNING_CHROME_PATH تنظیم کنید.",
        )
    profile = _profile_path()
    try:
        with sync_playwright() as playwright:
            context = playwright.chromium.launch_persistent_context(
                user_data_dir=str(profile),
                executable_path=chrome,
                headless=False,
                viewport={"width": 1365, "height": 900},
                accept_downloads=False,
            )
            try:
                page = context.pages[0] if context.pages else context.new_page()
                page.set_default_timeout(5000)
                page.goto("https://chatgpt.com/", wait_until="domcontentloaded", timeout=60000)
                try:
                    page.wait_for_load_state("networkidle", timeout=8000)
                except Exception:
                    pass
                page.wait_for_timeout(1200)
                if "login" in page.url.casefold() or "auth.openai.com" in page.url.casefold():
                    # Keep the visible browser open briefly so the user can complete a legitimate sign-in.
                    login_deadline = time.monotonic() + 180
                    while time.monotonic() < login_deadline:
                        if "login" not in page.url.casefold() and "auth.openai.com" not in page.url.casefold():
                            break
                        page.wait_for_timeout(1000)
                    if "login" in page.url.casefold() or "auth.openai.com" in page.url.casefold():
                        raise BrowserAutomationError(
                            "chatgpt_login_required",
                            "ورود به ChatGPT در ۳ دقیقه تکمیل نشد. اگر رابط عمومی بدون ورود کار می‌کند، دوباره تلاش کنید.",
                        )
                    page.wait_for_timeout(1500)
                previous = _visible_response(page)
                _send_prompt(page, prompt)
                return _wait_for_complete_answer(page, previous)
            finally:
                context.close()
    except BrowserAutomationError:
        raise
    except Exception as exc:
        raise BrowserAutomationError(
            "playwright_browser_error",
            f"خطای مرورگر Playwright: {str(exc)[:700]}",
        ) from exc


def _continue_lessons(course_id: int) -> None:
    """Keep the existing workflow: approve newly received topics and create lessons in batches of three."""
    course = service.get_course(course_id)
    pending_ids = [int(topic["id"]) for topic in course.get("topics", [])
                   if topic.get("approval_status") == "pending"]
    for offset in range(0, len(pending_ids), 500):
        batch = pending_ids[offset:offset + 500]
        if batch:
            service.decide_topics(batch, "approved")

    while True:
        course = service.get_course(course_id)
        eligible = [int(topic["id"]) for topic in course.get("topics", [])
                    if topic.get("approval_status") == "approved"
                    and int(topic.get("lesson_count") or 0) == 0]
        if not eligible:
            return
        topic_ids = eligible[:3]
        request_key = f"playwright-lessons-{course_id}-" + "-".join(map(str, topic_ids))
        run = service.create_run(course_id, run_type="lesson", topic_ids=topic_ids,
                                 selection_mode="selected", request_key=request_key)
        run_id = int(run["id"])
        service.start_run(run_id)
        try:
            answer = _browser_capture(run["prompt_text"])
            result = service.complete_run(run_id, answer)
        except BrowserAutomationError as exc:
            service.mark_run_needs_user_action(run_id, exc.code, str(exc))
            return
        if result.get("status") != "succeeded":
            return


def _worker(run_id: int, course_id: int, prompt: str) -> None:
    global _ACTIVE
    try:
        answer = _browser_capture(prompt)
        result = service.complete_run(run_id, answer)
        if result.get("status") == "succeeded":
            try:
                _continue_lessons(course_id)
            except Exception as exc:
                # The outline remains safely saved even if lesson follow-up fails.
                print(f"AI-learning lesson follow-up failed for course {course_id}: {exc}", flush=True)
    except BrowserAutomationError as exc:
        try:
            service.mark_run_needs_user_action(run_id, exc.code, str(exc))
        except Exception as mark_exc:
            print(f"Could not mark AI-learning run {run_id}: {mark_exc}", flush=True)
    except Exception as exc:
        try:
            service.mark_run_needs_user_action(
                run_id, "playwright_unexpected_error",
                f"خطای غیرمنتظره در دریافت پاسخ: {str(exc)[:700]}",
            )
        except Exception as mark_exc:
            print(f"Could not mark AI-learning run {run_id}: {mark_exc}", flush=True)
    finally:
        with _LOCK:
            _ACTIVE = False


def start_outline(course_id: int, request_key: str) -> dict[str, Any]:
    """Create and queue an outline run, then process it in a single background browser worker."""
    global _ACTIVE
    with _LOCK:
        if _ACTIVE:
            raise ValueError("یک کار مرورگر در حال اجراست؛ تا پایان آن صبر کنید.")
        run = service.create_run(
            course_id, run_type="outline", topic_ids=[], selection_mode="single",
            request_key=request_key,
        )
        if run["status"] not in {"queued", "running"}:
            raise ValueError("این درخواست قبلاً اجرا شده است؛ برای تلاش دوباره بازخوانی را دوباره بزنید.")
        service.start_run(int(run["id"]))
        _ACTIVE = True
        thread = threading.Thread(
            target=_worker, args=(int(run["id"]), course_id, str(run["prompt_text"])),
            name=f"ai-learning-browser-{run['id']}", daemon=True,
        )
        thread.start()
        return {"run_id": int(run["id"]), "status": "running", "provider": "ChatGPT visible UI"}
