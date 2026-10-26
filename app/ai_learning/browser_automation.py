"""Playwright-based visible-UI automation for AI-learning runs.

This module controls a dedicated persistent Chrome profile and only reads/writes
the visible ChatGPT page. It never uses private endpoints or session cookies.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

from . import service

_LOCK = threading.Lock()
_ACTIVE = False
_ACTIVE_RUN_ID: int | None = None
_ACTIVE_COURSE_ID: int | None = None
_CANCEL_EVENTS: dict[int, threading.Event] = {}
_RESPONSE_TIMEOUT_SECONDS = 600
_STABLE_SECONDS = 4.0
_LOG_LOCK = threading.Lock()
_LOGGER: logging.Logger | None = None


def _logger() -> logging.Logger:
    """Create a rotating diagnostic log without recording prompts or answer text."""
    global _LOGGER
    if _LOGGER is not None:
        return _LOGGER
    logger = logging.getLogger("ai_learning.browser_automation")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    if not logger.handlers:
        log_path = Path(os.getenv("AI_LEARNING_LOG_PATH", "data/logs/ai_learning_browser.log"))
        if not log_path.is_absolute():
            log_path = Path.cwd() / log_path
        log_path.parent.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(log_path, maxBytes=2_000_000, backupCount=3, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(handler)
        console = logging.StreamHandler()
        console.setFormatter(logging.Formatter("[AI-LEARNING] %(message)s"))
        logger.addHandler(console)
    _LOGGER = logger
    return logger


def _log_event(event: str, **fields: Any) -> None:
    """Write one structured JSON event to file and server console."""
    record = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "event": event, **fields}
    try:
        with _LOG_LOCK:
            _logger().info(json.dumps(record, ensure_ascii=False, default=str))
    except Exception as exc:
        print(f"[AI-LEARNING] diagnostic logging failed: {type(exc).__name__}: {exc}", flush=True)


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


def _assistant_message_count(page: Any) -> int:
    """Count assistant messages, falling back to visible response-copy controls on UI variants."""
    try:
        count = page.locator('[data-message-author-role="assistant"]').count()
        if count:
            return count
        return page.locator('button[aria-label="Copy response"],button[data-testid*="copy-turn"]').count()
    except Exception:
        return 0


def _visible_response_with_index(page: Any) -> tuple[str, int | None]:
    """Read the newest visible assistant response across supported ChatGPT DOM variants."""
    messages = page.locator('[data-message-author-role="assistant"]')
    try:
        count = messages.count()
    except Exception:
        count = 0
    for index in range(count - 1, -1, -1):
        try:
            text = messages.nth(index).inner_text(timeout=1500).strip()
        except Exception:
            continue
        if text:
            return text, index

    # Some public ChatGPT UI variants no longer expose data-message-author-role.
    # The visible "Copy response" action is attached to the assistant response.
    # Walk up from that action and select the smallest ancestor containing real text.
    copy_buttons = page.locator('button[aria-label="Copy response"],button[data-testid*="copy-turn"]')
    try:
        button_count = copy_buttons.count()
    except Exception:
        return "", None
    for index in range(button_count - 1, -1, -1):
        try:
            response_text = copy_buttons.nth(index).evaluate("""button => {
              let node = button;
              for (let depth = 0; node && depth < 8; depth++, node = node.parentElement) {
                const text = (node.innerText || '').trim();
                if (text.length >= 20) {
                  return text
                    .replace(/^(Copy response|Copy)\s*/i, '')
                    .replace(/\s+(Copy response|Read aloud|Good response|Bad response|Regenerate)\s*$/i, '')
                    .trim();
                }
              }
              return '';
            }""")
            response_text = (response_text or "").strip()
            if response_text:
                return response_text, index
        except Exception:
            continue
    return "", None


def _visible_response(page: Any) -> str:
    """Read the newest non-empty assistant message, ignoring empty streaming placeholders."""
    return _visible_response_with_index(page)[0]


def _dom_diagnostics(page: Any) -> dict[str, Any]:
    """Collect structural UI diagnostics without storing conversation text."""
    try:
        return page.evaluate("""() => {
          const count = selector => { try { return document.querySelectorAll(selector).length; } catch (_) { return -1; } };
          const visibleLabels = Array.from(document.querySelectorAll('button,[role=alert],[role=status]'))
            .filter(el => !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length))
            .slice(-20).map(el => ({tag: el.tagName.toLowerCase(), testid: el.getAttribute('data-testid') || '',
              aria: el.getAttribute('aria-label') || '', title: el.getAttribute('title') || '',
              role: el.getAttribute('role') || '', textLength: (el.innerText || '').trim().length}));
          const roleCounts = {};
          document.querySelectorAll('[data-message-author-role]').forEach(el => {
            const role = el.getAttribute('data-message-author-role') || '(empty)';
            roleCounts[role] = (roleCounts[role] || 0) + 1;
          });
          return {url: location.href, title: document.title, readyState: document.readyState,
            bodyTextLength: (document.body?.innerText || '').length,
            mainTextLength: (document.querySelector('main')?.innerText || '').length,
            assistantNodes: count('[data-message-author-role="assistant"]'),
            allMessageRoleNodes: count('[data-message-author-role]'), roleCounts,
            conversationTurns: count('[data-testid^="conversation-turn"]'),
            messageContentNodes: count('[data-message-content]'), mainNodes: count('main'),
            textareas: count('textarea'), contenteditables: count('[contenteditable="true"]'),
            visibleLabels};
        }""")
    except Exception as exc:
        return {"probe_error": f"{type(exc).__name__}: {str(exc)[:180]}"}


def _send_prompt(page: Any, prompt: str) -> None:
    composer_candidates = [
        "#prompt-textarea",
        'div[contenteditable="true"][data-testid="prompt-textarea"]',
        'div[contenteditable="true"][role="textbox"]',
        'textarea[placeholder]',
    ]
    _log_event("composer_search_started", url=page.url, selectors=len(composer_candidates))
    composer = None
    chosen_selector = None
    for selector in composer_candidates:
        locator = page.locator(selector).last
        try:
            if locator.count() and locator.is_visible(timeout=1000):
                composer = locator
                chosen_selector = selector
                break
        except Exception:
            continue
    if composer is None:
        _log_event("composer_not_found", url=page.url)
        raise BrowserAutomationError(
            "chatgpt_composer_not_found",
            "کادر نوشتن ChatGPT پیدا نشد؛ صفحه را بررسی کنید و دوباره تلاش کنید.",
        )
    _log_event("composer_found", selector=chosen_selector, prompt_chars=len(prompt))
    try:
        composer.click(timeout=5000)
        composer.fill(prompt, timeout=10000)
        _log_event("prompt_filled", selector=chosen_selector, prompt_chars=len(prompt))
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
                _log_event("prompt_submitted", method="button", selector=selector)
                return
        except Exception:
            continue
    # Keyboard submission is only used after the visible composer was positively found.
    try:
        composer.press("Enter", timeout=3000)
        _log_event("prompt_submitted", method="enter")
    except Exception as exc:
        raise BrowserAutomationError(
            "chatgpt_send_not_found",
            "دکمه ارسال پیدا نشد و ارسال با صفحه‌کلید هم موفق نبود.",
        ) from exc


def _wait_for_complete_answer(page: Any, previous_answer: str, cancel_event: threading.Event | None = None, previous_message_count: int | None = None) -> str:
    started = time.monotonic()
    deadline = started + _RESPONSE_TIMEOUT_SECONDS
    next_diagnostic_at = 0.0
    _log_event("response_wait_started", url=page.url, previous_message_count=previous_message_count,
               previous_answer_chars=len(previous_answer), timeout_seconds=_RESPONSE_TIMEOUT_SECONDS,
               dom=_dom_diagnostics(page))
    last_text = ""
    stable_since: float | None = None
    last_stop_visible = False
    while time.monotonic() < deadline:
        # The pause flag is stored in SQLite, so it survives a process restart.
        try:
            paused = service.get_learning_control()["paused"]
        except Exception:
            paused = False
        if paused:
            page.wait_for_timeout(1000)
            deadline += 1.0
            continue
        if cancel_event is not None and cancel_event.is_set():
            _log_event("response_wait_cancelled", elapsed_seconds=round(time.monotonic() - started, 1))
            raise BrowserAutomationError("run_cancelled", "اجرای دریافت پاسخ به درخواست کاربر متوقف شد.")
        try:
            current, current_message_index = _visible_response_with_index(page)
            new_message_seen = (
                previous_message_count is not None
                and current_message_index is not None
                and current_message_index >= previous_message_count
            )
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
            elapsed = time.monotonic() - started
            if elapsed >= next_diagnostic_at:
                _log_event("response_poll", elapsed_seconds=round(elapsed, 1),
                           assistant_message_count=_assistant_message_count(page),
                           current_message_index=current_message_index, current_chars=len(current),
                           new_message_seen=new_message_seen, stop_visible=stop_visible,
                           stable_seconds=round(time.monotonic() - stable_since, 1) if stable_since else 0,
                           dom=_dom_diagnostics(page))
                next_diagnostic_at = elapsed + 10.0
            # Some ChatGPT UI versions leave a stale/hidden stop-generation control
            # visible after the answer has finished. Track text stability independently
            # and use a longer grace period when that control remains visible.
            if current and (current != previous_answer or new_message_seen) and current == last_text:
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
                    _log_event("response_accepted", elapsed_seconds=round(time.monotonic() - started, 1),
                               answer_chars=len(current), message_index=current_message_index,
                               stop_visible=stop_visible, stable_seconds=round(stable_for, 1))
                    return current
            else:
                stable_since = None
            last_text = current
            last_stop_visible = stop_visible
            # Keep the wait inside the guarded block: Playwright raises here if the
            # page/context/browser closes between polling iterations.
            page.wait_for_timeout(1000)
        except BrowserAutomationError:
            raise
        except Exception as exc:
            # If the browser closes after a complete answer was already stable long
            # enough, retain that answer instead of discarding it because the final
            # polling sleep failed.
            _, current_message_index = _visible_response_with_index(page)
            new_message_seen = (
                previous_message_count is not None
                and current_message_index is not None
                and current_message_index >= previous_message_count
            )
            if last_text and (last_text != previous_answer or new_message_seen) and stable_since is not None:
                stable_for = time.monotonic() - stable_since
                required_stability = 25.0 if last_stop_visible else _STABLE_SECONDS
                if stable_for >= required_stability and len(last_text) >= 20:
                    return last_text
            _log_event("response_poll_exception", elapsed_seconds=round(time.monotonic() - started, 1),
                       exception_type=type(exc).__name__, message=str(exc)[:300])
            raise BrowserAutomationError(
                "chatgpt_page_closed",
                "صفحه یا مرورگر ChatGPT هنگام دریافت پاسخ بسته شد. اگر پاسخ هنوز در صفحه قابل مشاهده است، مرورگر را باز نگه دارید و دوباره تلاش کنید.",
            ) from exc
    _log_event("response_timeout", elapsed_seconds=round(time.monotonic() - started, 1),
               last_chars=len(last_text), last_message_count=_assistant_message_count(page),
               last_stop_visible=last_stop_visible, stable_since_set=stable_since is not None)
    raise BrowserAutomationError(
        "chatgpt_response_timeout",
        "در مهلت ۱۰ دقیقه‌ای پاسخ کامل و پایدار از ChatGPT دریافت نشد. اجرای کار برای بررسی در داشبورد نگه داشته شد.",
    )


def _browser_capture(prompt: str, cancel_event: threading.Event | None = None) -> str:
    capture_started = time.monotonic()
    _log_event("capture_started", prompt_chars=len(prompt), cancelled=bool(cancel_event and cancel_event.is_set()))
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
    _log_event("browser_configured", chrome_path=chrome, profile_path=str(profile))
    try:
        with sync_playwright() as playwright:
            _log_event("browser_launch_started")
            context = playwright.chromium.launch_persistent_context(
                user_data_dir=str(profile),
                executable_path=chrome,
                headless=False,
                viewport={"width": 1365, "height": 900},
                accept_downloads=False,
            )
            _log_event("browser_launch_succeeded", open_pages=len(context.pages))
            try:
                page = context.pages[0] if context.pages else context.new_page()
                _log_event("page_selected", url=page.url, page_count=len(context.pages))
                page.on("close", lambda *_: _log_event("page_closed_event", url=page.url))
                page.on("crash", lambda *_: _log_event("page_crashed_event", url=page.url))
                page.on("pageerror", lambda error: _log_event("page_javascript_error", message=str(error)[:300]))
                page.on("console", lambda message: _log_event("page_console_error", message=message.text[:300]) if message.type == "error" else None)
                page.on("requestfailed", lambda request: _log_event("browser_request_failed", url=request.url.split("?")[0][:300], error=request.failure or "unknown") if any(host in request.url for host in ("chatgpt.com", "openai.com")) else None)
                page.set_default_timeout(5000)
                if cancel_event is not None and cancel_event.is_set():
                    raise BrowserAutomationError("run_cancelled", "اجرای دریافت پاسخ به درخواست کاربر متوقف شد.")
                _log_event("navigation_started", target="https://chatgpt.com/")
                page.goto("https://chatgpt.com/", wait_until="domcontentloaded", timeout=60000)
                _log_event("navigation_domcontentloaded", url=page.url)
                try:
                    page.wait_for_load_state("networkidle", timeout=8000)
                except Exception:
                    pass
                page.wait_for_timeout(1200)
                _log_event("page_ready", url=page.url, assistant_message_count=_assistant_message_count(page), dom=_dom_diagnostics(page))
                if "login" in page.url.casefold() or "auth.openai.com" in page.url.casefold():
                    _log_event("login_required", url=page.url)
                    # Keep the visible browser open briefly so the user can complete a legitimate sign-in.
                    login_deadline = time.monotonic() + 180
                    while time.monotonic() < login_deadline:
                        if cancel_event is not None and cancel_event.is_set():
                            raise BrowserAutomationError("run_cancelled", "اجرای دریافت پاسخ به درخواست کاربر متوقف شد.")
                        if "login" not in page.url.casefold() and "auth.openai.com" not in page.url.casefold():
                            break
                        page.wait_for_timeout(1000)
                    if "login" in page.url.casefold() or "auth.openai.com" in page.url.casefold():
                        raise BrowserAutomationError(
                            "chatgpt_login_required",
                            "ورود به ChatGPT در ۳ دقیقه تکمیل نشد. اگر رابط عمومی بدون ورود کار می‌کند، دوباره تلاش کنید.",
                        )
                    page.wait_for_timeout(1500)
                if cancel_event is not None and cancel_event.is_set():
                    raise BrowserAutomationError("run_cancelled", "اجرای دریافت پاسخ به درخواست کاربر متوقف شد.")
                previous_message_count = _assistant_message_count(page)
                previous = _visible_response(page)
                _log_event("pre_submit_snapshot", url=page.url, assistant_message_count=previous_message_count,
                           previous_answer_chars=len(previous), dom=_dom_diagnostics(page))
                _send_prompt(page, prompt)
                answer = _wait_for_complete_answer(
                    page, previous, cancel_event, previous_message_count=previous_message_count
                )
                _log_event("capture_succeeded", elapsed_seconds=round(time.monotonic() - capture_started, 1),
                           answer_chars=len(answer))
                return answer
            finally:
                context.close()
    except BrowserAutomationError as exc:
        _log_event("capture_failed", elapsed_seconds=round(time.monotonic() - capture_started, 1),
                   error_code=exc.code, message=str(exc)[:500])
        raise
    except Exception as exc:
        _log_event("capture_failed", elapsed_seconds=round(time.monotonic() - capture_started, 1),
                   error_code="playwright_browser_error", exception_type=type(exc).__name__, message=str(exc)[:500])
        raise BrowserAutomationError(
            "playwright_browser_error",
            f"خطای مرورگر Playwright: {str(exc)[:700]}",
        ) from exc


def _continue_lessons(course_id: int, cancel_event: threading.Event) -> None:
    """Approve captured topics and fetch lessons one topic at a time for safe pause/resume."""
    global _ACTIVE_RUN_ID
    course = service.get_course(course_id)
    pending_ids = [int(topic["id"]) for topic in course.get("topics", [])
                   if topic.get("approval_status") == "pending"]
    for offset in range(0, len(pending_ids), 500):
        batch = pending_ids[offset:offset + 500]
        if batch:
            service.decide_topics(batch, "approved")

    while True:
        if service.get_learning_control()["paused"]:
            _log_event("lesson_followup_paused", course_id=course_id)
            return
        course = service.get_course(course_id)
        eligible = [int(topic["id"]) for topic in course.get("topics", [])
                    if topic.get("approval_status") == "approved"
                    and topic.get("processing_status") != "paused"
                    and int(topic.get("lesson_content_ready_count") or 0) == 0]
        if not eligible:
            return
        # One topic per run makes per-topic pause/cancel deterministic and recoverable.
        topic_ids = eligible[:1]
        request_key = f"playwright-lessons-{course_id}-" + "-".join(map(str, topic_ids))
        run = service.create_run(course_id, run_type="lesson", topic_ids=topic_ids,
                                 selection_mode="selected", request_key=request_key)
        run_id = int(run["id"])
        service.start_run(run_id)
        with _LOCK:
            previous_active_run_id = _ACTIVE_RUN_ID
            _ACTIVE_RUN_ID = run_id
            _CANCEL_EVENTS[run_id] = cancel_event
        try:
            answer = _browser_capture(run["prompt_text"], cancel_event)
            result = service.complete_run(run_id, answer)
        except BrowserAutomationError as exc:
            service.mark_run_needs_user_action(run_id, exc.code, str(exc))
            return
        finally:
            with _LOCK:
                _CANCEL_EVENTS.pop(run_id, None)
                if _ACTIVE_RUN_ID == run_id:
                    _ACTIVE_RUN_ID = previous_active_run_id
        if result.get("status") != "succeeded":
            return


def _worker(run_id: int, course_id: int, prompt: str, cancel_event: threading.Event) -> None:
    global _ACTIVE, _ACTIVE_RUN_ID, _ACTIVE_COURSE_ID
    worker_started = time.monotonic()
    _log_event("worker_started", run_id=run_id, course_id=course_id, prompt_chars=len(prompt))
    try:
        answer = _browser_capture(prompt, cancel_event)
        _log_event("database_completion_started", run_id=run_id, answer_chars=len(answer))
        result = service.complete_run(run_id, answer)
        _log_event("database_completion_finished", run_id=run_id, status=result.get("status"),
                   elapsed_seconds=round(time.monotonic() - worker_started, 1))
        if result.get("status") == "succeeded":
            try:
                _continue_lessons(course_id, cancel_event)
            except Exception as exc:
                # The outline remains safely saved even if lesson follow-up fails.
                print(f"AI-learning lesson follow-up failed for course {course_id}: {exc}", flush=True)
    except BrowserAutomationError as exc:
        _log_event("worker_browser_error", run_id=run_id, error_code=exc.code, message=str(exc)[:500],
                   elapsed_seconds=round(time.monotonic() - worker_started, 1))
        try:
            service.mark_run_needs_user_action(run_id, exc.code, str(exc))
        except Exception as mark_exc:
            print(f"Could not mark AI-learning run {run_id}: {mark_exc}", flush=True)
    except Exception as exc:
        _log_event("worker_unexpected_error", run_id=run_id, exception_type=type(exc).__name__,
                   message=str(exc)[:500], elapsed_seconds=round(time.monotonic() - worker_started, 1))
        try:
            service.mark_run_needs_user_action(
                run_id, "playwright_unexpected_error",
                f"خطای غیرمنتظره در دریافت پاسخ: {str(exc)[:700]}",
            )
        except Exception as mark_exc:
            print(f"Could not mark AI-learning run {run_id}: {mark_exc}", flush=True)
    finally:
        _log_event("worker_finished", run_id=run_id, elapsed_seconds=round(time.monotonic() - worker_started, 1))
        with _LOCK:
            _ACTIVE = False
            _ACTIVE_RUN_ID = None
            _ACTIVE_COURSE_ID = None
            _CANCEL_EVENTS.pop(run_id, None)


def start_outline(course_id: int, request_key: str) -> dict[str, Any]:
    """Create and queue an outline run, then process it in a single background browser worker."""
    global _ACTIVE, _ACTIVE_RUN_ID, _ACTIVE_COURSE_ID
    if service.get_learning_control()["paused"]:
        raise ValueError("مکث سراسری فعال است؛ برای شروع یا ادامه ابتدا «ادامه / بازیابی» را بزنید.")
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
        _ACTIVE_RUN_ID = int(run["id"])
        _ACTIVE_COURSE_ID = course_id
        cancel_event = threading.Event()
        _CANCEL_EVENTS[int(run["id"])] = cancel_event
        thread = threading.Thread(
            target=_worker, args=(int(run["id"]), course_id, str(run["prompt_text"]), cancel_event),
            name=f"ai-learning-browser-{run['id']}", daemon=True,
        )
        thread.start()
        return {"run_id": int(run["id"]), "status": "running", "provider": "ChatGPT visible UI"}


def cancel_run(run_id: int) -> dict[str, Any]:
    """Signal the local browser worker and persist cancellation of its run."""
    with _LOCK:
        event = _CANCEL_EVENTS.get(run_id)
        if event is not None:
            event.set()
    return service.cancel_run(run_id)


def set_topic_paused(topic_id: int, paused: bool) -> dict[str, Any]:
    """Pause one topic and cancel its active single-topic run if necessary."""
    result = service.set_topic_paused(topic_id, paused)
    if paused:
        with _LOCK:
            active_run_id = _ACTIVE_RUN_ID
        if active_run_id is not None:
            try:
                run = service.get_run(active_run_id)
                if any(int(item["topic_id"]) == topic_id for item in run.get("topics", [])):
                    cancel_run(active_run_id)
                    _log_event("topic_run_paused", topic_id=topic_id, run_id=active_run_id)
            except Exception as exc:
                _log_event("topic_pause_cancel_failed", topic_id=topic_id, error=str(exc)[:300])
    return result


def pause_learning() -> dict[str, Any]:
    """Pause all ChatGPT outline and lesson capture, preserving completed database work."""
    state = service.set_learning_paused(True, "توقف سراسری توسط کاربر")
    _log_event("learning_paused", active_run_id=_ACTIVE_RUN_ID, active_course_id=_ACTIVE_COURSE_ID)
    return {**state, "active": _ACTIVE, "active_run_id": _ACTIVE_RUN_ID}


def resume_learning(course_id: int | None = None) -> dict[str, Any]:
    """Resume an active worker, recover a persisted in-flight run, or continue remaining lesson batches."""
    global _ACTIVE, _ACTIVE_RUN_ID, _ACTIVE_COURSE_ID
    service.set_learning_paused(False)
    with _LOCK:
        if _ACTIVE:
            return {"status": "resumed", "active": True, "run_id": _ACTIVE_RUN_ID,
                    "course_id": _ACTIVE_COURSE_ID, "message": "فرایند فعال ادامه پیدا می‌کند."}

        candidates: list[dict[str, Any]] = []
        if course_id is not None:
            candidates = service.list_course_runs(course_id, 50)
        else:
            for course in service.list_courses(100):
                candidates.extend(service.list_course_runs(int(course["id"]), 50))
        in_flight = next((item for item in candidates if item.get("status") in {"running", "queued"}), None)
        if in_flight is not None:
            run = service.start_run(int(in_flight["id"]))
            run = service.get_run(int(in_flight["id"]))
            run_id = int(run["id"])
            target_course = int(run["course_id"])
            _ACTIVE = True
            _ACTIVE_RUN_ID = run_id
            _ACTIVE_COURSE_ID = target_course
            cancel_event = threading.Event()
            _CANCEL_EVENTS[run_id] = cancel_event
            thread = threading.Thread(
                target=_worker, args=(run_id, target_course, str(run["prompt_text"]), cancel_event),
                name=f"ai-learning-resume-{run_id}", daemon=True,
            )
            thread.start()
            _log_event("learning_run_recovered", run_id=run_id, course_id=target_course, run_type=run["run_type"])
            return {"status": "resumed", "active": True, "run_id": run_id, "course_id": target_course,
                    "message": "اجرای ناتمام دوباره آغاز شد؛ پاسخ‌های ثبت‌شده حفظ شده‌اند."}

        courses = ([service.get_course(course_id)] if course_id is not None
                   else [service.get_course(int(item["id"])) for item in service.list_courses(100)])
        for course in courses:
            cid = int(course["id"])
            eligible = [int(topic["id"]) for topic in course.get("topics", [])
                        if topic.get("approval_status") == "approved"
                        and topic.get("processing_status") != "paused"
                        and int(topic.get("lesson_count") or 0) == 0]
            if not eligible:
                continue
            # One topic per run makes per-topic pause/cancel deterministic and recoverable.
            topic_ids = eligible[:1]
            request_key = f"playwright-lessons-{cid}-" + "-".join(map(str, topic_ids))
            request_key += "-resume-" + str(int(time.time()))
            run = service.create_run(cid, run_type="lesson", topic_ids=topic_ids,
                                     selection_mode="selected", request_key=request_key)
            run = service.start_run(int(run["id"]))
            run_id = int(run["id"])
            _ACTIVE = True
            _ACTIVE_RUN_ID = run_id
            _ACTIVE_COURSE_ID = cid
            cancel_event = threading.Event()
            _CANCEL_EVENTS[run_id] = cancel_event
            thread = threading.Thread(
                target=_worker, args=(run_id, cid, str(run["prompt_text"]), cancel_event),
                name=f"ai-learning-resume-{run_id}", daemon=True,
            )
            thread.start()
            _log_event("lesson_workflow_resumed", run_id=run_id, course_id=cid, topic_ids=topic_ids)
            return {"status": "resumed", "active": True, "run_id": run_id, "course_id": cid,
                    "message": "تولید درس‌ها از سرفصل‌های تأییدشده‌ای که هنوز درس ندارند ادامه یافت."}
        return {"status": "idle", "active": False, "message": "کار ناتمامی برای ادامه پیدا نشد."}
