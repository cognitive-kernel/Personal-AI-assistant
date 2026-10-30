"""Playwright-based visible-UI automation for AI-learning runs.

This module controls a dedicated persistent Chrome profile and only reads/writes
the visible ChatGPT page. It never uses private endpoints or session cookies.
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
import uuid
from logging.handlers import RotatingFileHandler
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable

from . import service

_LOCK = threading.Lock()
_WORKER_OWNER = f"{os.getpid()}-{uuid.uuid4().hex}"
_WORKER_LEASE_SECONDS = 90
_WORKER_LEASE_HEARTBEAT_SECONDS = 15.0
_WORKER_LEASE_EPOCH: int | None = None
_ACTIVE = False
_ACTIVE_RUN_ID: int | None = None
_ACTIVE_COURSE_ID: int | None = None
_CANCEL_EVENTS: dict[int, threading.Event] = {}
_SHUTTING_DOWN = False
_RESPONSE_TIMEOUT_SECONDS = 1800
_STABLE_SECONDS = 20.0
_MAX_LESSON_CONTINUATIONS = 8
_MIN_COMPLETE_LESSON_WORDS = 1200
_LOG_LOCK = threading.Lock()
_LOGGER: logging.Logger | None = None
_BROWSER_SESSION = threading.local()
_MAX_CONTEXT_RELAUNCHES = 1
# Only failures that can reasonably clear without operator intervention are retried
# across worker sessions. Authentication, human verification, missing dependencies,
# browser closure loops, and content-quality failures remain manual-action states.
def _release_worker_lease() -> bool:
    """Release only this process's current lease generation."""
    global _WORKER_LEASE_EPOCH
    epoch = _WORKER_LEASE_EPOCH
    if epoch is None:
        return False
    try:
        released = service.release_worker_lease(_WORKER_OWNER, epoch)
    finally:
        _WORKER_LEASE_EPOCH = None
    return released


def _assert_worker_lease(epoch: int | None = None) -> None:
    """Fence provider results and database writes after process ownership is lost."""
    expected = _WORKER_LEASE_EPOCH if epoch is None else epoch
    if expected is None:
        return
    if not service.owns_worker_lease(_WORKER_OWNER, expected):
        raise BrowserAutomationError(
            "worker_lease_lost",
            "مالکیت اجرای مرورگر از دست رفته است؛ نتیجهٔ این worker ثبت نمی‌شود و صف باید از مالک فعلی بازیابی شود.",
        )


def _worker_lease_kwargs(epoch: int | None = None) -> dict[str, Any]:
    """Build the optional transaction-level fencing arguments for worker writes."""
    expected = _WORKER_LEASE_EPOCH if epoch is None else epoch
    if expected is None:
        return {}
    return {
        "worker_lease_owner": _WORKER_OWNER,
        "worker_lease_epoch": expected,
    }


def _maintain_worker_lease(
    cancel_event: threading.Event,
    stop_event: threading.Event,
    epoch: int,
) -> None:
    """Renew the process-wide browser lease and observe cross-process pause/cancel requests."""
    while not stop_event.wait(_WORKER_LEASE_HEARTBEAT_SECONDS):
        try:
            if not service.renew_worker_lease(
                _WORKER_OWNER, epoch, lease_seconds=_WORKER_LEASE_SECONDS
            ):
                cancel_event.set()
                _log_event("worker_lease_lost", lease_epoch=epoch)
                return
            with _LOCK:
                active_run_id = _ACTIVE_RUN_ID
            if active_run_id is not None:
                active_run = service.get_run(active_run_id)
                status = active_run.get("status")
                paused_topics = [
                    int(topic["topic_id"]) for topic in active_run.get("topics", [])
                    if topic.get("processing_status") == "paused"
                ]
                if status in {"cancelled", "queued", "failed", "needs_user_action"} or paused_topics:
                    cancel_event.set()
                    _log_event(
                        "worker_control_observed",
                        run_id=active_run_id,
                        status=status,
                        paused_topic_ids=paused_topics,
                    )
                    return
        except Exception as exc:
            # A transient DB lock must not immediately abandon a still-valid lease.
            _log_event(
                "worker_lease_heartbeat_error",
                lease_epoch=epoch,
                exception_type=type(exc).__name__,
                message=str(exc)[:300],
            )


_AUTOMATIC_RETRY_DELAYS_SECONDS = {
    "chatgpt_network_error": (60, 300),
    "chatgpt_page_closed": (60, 300),
    "chatgpt_response_timeout": (120, 600),
    "provider_rate_limited": (300, 900),
    "playwright_browser_error": (60, 300),
}


@contextmanager
def _browser_session(sync_playwright_factory: Any, chrome: str, profile: Path):
    """Reuse one visible Chrome context for all captures performed by the same worker thread."""
    if getattr(_BROWSER_SESSION, "reuse_enabled", False):
        playwright = getattr(_BROWSER_SESSION, "playwright", None)
        context = getattr(_BROWSER_SESSION, "context", None)
        context_lost = bool(getattr(_BROWSER_SESSION, "context_closed", False))
        if context is not None and not context_lost:
            try:
                list(context.pages)
            except Exception:
                context_lost = True
        if context_lost:
            context = None
        if context is None:
            if context_lost:
                relaunches = int(getattr(_BROWSER_SESSION, "context_relaunches", 0))
                if relaunches >= _MAX_CONTEXT_RELAUNCHES:
                    raise BrowserAutomationError(
                        "chatgpt_browser_closed",
                        "پنجرهٔ Chrome دوباره بسته شد؛ یک بار تلاش کنترل‌شده برای بازگشایی انجام شد و برای جلوگیری از چرخهٔ بازشدن پنجره‌ها، بازیابی دستی لازم است.",
                    )
                _BROWSER_SESSION.context_relaunches = relaunches + 1
                _log_event(
                    "browser_context_lost_relaunching",
                    relaunch_attempt=_BROWSER_SESSION.context_relaunches,
                )
                _close_browser_session()
                _BROWSER_SESSION.launch_attempted = False
            elif getattr(_BROWSER_SESSION, "launch_attempted", False):
                raise BrowserAutomationError(
                    "chatgpt_browser_closed",
                    "راه‌اندازی Chrome در این worker موفق نشد؛ برای جلوگیری از بازشدن مکرر پنجره‌ها، بازیابی دستی لازم است.",
                )
            _BROWSER_SESSION.launch_attempted = True
            if not context_lost:
                _close_browser_session()
            _log_event("browser_launch_started", reason="new_worker_session")
            playwright = sync_playwright_factory().start()
            try:
                context = playwright.chromium.launch_persistent_context(
                    user_data_dir=str(profile),
                    executable_path=chrome,
                    headless=False,
                    args=["--no-first-run"],
                    viewport={"width": 1365, "height": 900},
                    accept_downloads=False,
                )
            except Exception:
                try:
                    playwright.stop()
                except Exception:
                    pass
                raise
            _BROWSER_SESSION.playwright = playwright
            _BROWSER_SESSION.context = context
            _BROWSER_SESSION.instrumented_page = None
            _BROWSER_SESSION.context_closed = False
            try:
                context.on(
                    "close",
                    lambda *_: setattr(_BROWSER_SESSION, "context_closed", True),
                )
            except Exception as exc:
                _log_event(
                    "browser_context_close_listener_failed",
                    exception_type=type(exc).__name__,
                    message=str(exc)[:200],
                )
            _log_event("browser_launch_succeeded", open_pages=len(context.pages))
        else:
            _log_event("browser_session_reused", open_pages=len(context.pages))
        yield playwright, context
        return

    with sync_playwright_factory() as playwright:
        _log_event("browser_launch_started")
        context = playwright.chromium.launch_persistent_context(
            user_data_dir=str(profile),
            executable_path=chrome,
            headless=False,
            args=["--no-first-run"],
            viewport={"width": 1365, "height": 900},
            accept_downloads=False,
        )
        _log_event("browser_launch_succeeded", open_pages=len(context.pages))
        yield playwright, context


def _close_browser_session() -> None:
    """Release the worker's Chrome context and Playwright driver without leaking tabs."""
    context = getattr(_BROWSER_SESSION, "context", None)
    playwright = getattr(_BROWSER_SESSION, "playwright", None)
    _BROWSER_SESSION.context = None
    _BROWSER_SESSION.playwright = None
    _BROWSER_SESSION.instrumented_page = None
    if context is not None:
        try:
            context.close()
        except Exception as exc:
            _log_event("browser_context_close_failed", exception_type=type(exc).__name__, message=str(exc)[:200])
    if playwright is not None:
        try:
            playwright.stop()
        except Exception as exc:
            _log_event("playwright_stop_failed", exception_type=type(exc).__name__, message=str(exc)[:200])
    if context is not None or playwright is not None:
        _log_event("browser_session_closed")



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
    def __init__(self, code: str, message: str, *, partial_response: str | None = None):
        super().__init__(message)
        self.code = code
        self.partial_response = partial_response


def _provider_gate_error(url: str, visible_text: str) -> BrowserAutomationError | None:
    """Classify explicit visible provider gates; never attempt to bypass them."""
    lowered_url = (url or "").casefold()
    text = " ".join((visible_text or "").casefold().split())
    if "auth.openai.com" in lowered_url or "/auth/login" in lowered_url or "/login" in lowered_url:
        return BrowserAutomationError(
            "guest_send_unavailable",
            "ChatGPT برای این درخواست به صفحهٔ ورود هدایت کرد؛ ورود باید توسط کاربر انجام شود.",
        )
    human_gate_phrases = (
        "verify you are human",
        "confirm you're not a robot",
        "confirm you are not a robot",
        "complete the security check",
        "captcha verification required",
    )
    if any(phrase in text for phrase in human_gate_phrases):
        return BrowserAutomationError(
            "human_verification_required",
            "ChatGPT تأیید انسانی می‌خواهد؛ آن را در مرورگر انجام دهید و سپس کار را بازیابی کنید.",
        )
    network_error_phrases = (
        "network error",
        "connection lost",
        "connection interrupted",
        "trying to reconnect",
        "unable to connect",
        "connection failed",
        "something went wrong",
    )
    if any(phrase in text for phrase in network_error_phrases):
        return BrowserAutomationError(
            "chatgpt_network_error",
            "ارتباط مرورگر با ChatGPT قطع یا ناپایدار شده است. اتصال اینترنت و VPN را بررسی کنید؛ سامانه پس از مکث‌های ۶۰ و ۳۰۰ ثانیه‌ای حداکثر دو بار تلاش مجدد می‌کند و اگر مشکل باقی بماند، وضعیت نیازمند بررسی دستی می‌شود. خطا در data/logs/ai_learning_browser.log ثبت شده است.",
        )
    rate_limit_phrases = (
        "you've reached the limit of messages",
        "you have reached the limit of messages",
        "message limit reached",
    )
    explicit_too_many_requests = (
        text.startswith("too many requests")
        and ("try again later" in text or text in {"too many requests", "too many requests."})
    )
    explicit_retry_later = (
        text.startswith("try again later")
        and len(text) < 180
    )
    if any(phrase in text for phrase in rate_limit_phrases) or explicit_too_many_requests or explicit_retry_later:
        return BrowserAutomationError(
            "provider_rate_limited",
            "ChatGPT محدودیت موقت درخواست اعمال کرده است؛ سامانه پس از ۵ و ۱۵ دقیقه حداکثر دو بار تلاش مجدد می‌کند و در صورت تداوم محدودیت، بررسی دستی لازم است.",
        )
    return None


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
            response_text = copy_buttons.nth(index).evaluate(r"""button => {
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



def _is_anonymous_landing_text(url: str, body_text: str) -> bool:
    """Detect ChatGPT's logged-out welcome page instead of a usable conversation."""
    url_lower = (url or "").casefold()
    body_lower = (body_text or "").casefold()
    login_promo = (
        "get responses tailored to you" in body_lower
        and "log in to get answers" in body_lower
    )
    return login_promo or (
        "noauth_recent_chat_failed" in url_lower
        and ("log in" in body_lower or "where should we begin" in body_lower)
    )


def _anonymous_landing_visible(page: Any) -> bool:
    try:
        body_text = page.locator("body").inner_text(timeout=2000)
    except Exception:
        body_text = ""
    return _is_anonymous_landing_text(str(getattr(page, "url", "")), body_text)


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
        _log_event("prompt_fill_failed", selector=chosen_selector, exception_type=type(exc).__name__,
                   message=str(exc)[:300], dom=_dom_diagnostics(page))
        raise BrowserAutomationError(
            "chatgpt_composer_unavailable",
            "کادر نوشتن ChatGPT قابل استفاده نیست؛ جزئیات در data/logs/ai_learning_browser.log ثبت شده است. اگر صفحه ورود یا تأیید انسانی می‌خواهد، آن را در مرورگر تکمیل کنید.",
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
            count = button.count()
            visible = bool(count and button.is_visible(timeout=800))
            enabled = bool(visible and button.is_enabled())
            _log_event("send_button_probe", selector=selector, count=count, visible=visible, enabled=enabled)
            if enabled:
                button.click(timeout=5000)
                _log_event("prompt_submitted", method="button", selector=selector)
                return
        except Exception as exc:
            _log_event("send_button_probe_failed", selector=selector, exception_type=type(exc).__name__,
                       message=str(exc)[:240])
            continue
    # Keyboard submission is only used after the visible composer was positively found.
    try:
        _log_event("send_button_unavailable_keyboard_fallback", selector=chosen_selector,
                   dom=_dom_diagnostics(page))
        composer.press("Enter", timeout=3000)
        _log_event("prompt_submitted", method="enter", selector=chosen_selector)
    except Exception as exc:
        _log_event("prompt_submit_failed", selector=chosen_selector, exception_type=type(exc).__name__,
                   message=str(exc)[:300], dom=_dom_diagnostics(page))
        raise BrowserAutomationError(
            "chatgpt_send_not_found",
            "دکمه ارسال پیدا نشد و ارسال با صفحه‌کلید هم موفق نبود؛ جزئیات در data/logs/ai_learning_browser.log ثبت شده است.",
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
    last_continue_signature: tuple[int, str] | None = None
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
            # Inspect only visible alert/error surfaces during streaming; do not scan
            # the whole conversation, which may legitimately discuss these phrases.
            try:
                alerts = page.locator('[role="alert"],[data-testid*="error"]')
                for alert_index in range(max(0, alerts.count() - 3), alerts.count()):
                    try:
                        alert_text = alerts.nth(alert_index).inner_text(timeout=300)
                    except Exception:
                        continue
                    gate_error = _provider_gate_error(page.url, alert_text)
                    if gate_error is not None:
                        _log_event("provider_gate_detected", error_code=gate_error.code, url=page.url)
                        raise gate_error
            except BrowserAutomationError:
                raise
            except Exception:
                pass
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
            continue_button = None
            continue_button_text = ""
            for selector in (
                'button:has-text("Continue generating")',
                'button:has-text("Continue response")',
                'button:has-text("ادامه تولید")',
            ):
                try:
                    candidate = page.locator(selector).last
                    if candidate.is_visible(timeout=100):
                        continue_button = candidate
                        continue_button_text = (candidate.inner_text(timeout=200) or "").strip()[:80]
                        break
                except Exception:
                    continue
            elapsed = time.monotonic() - started
            if elapsed >= next_diagnostic_at:
                _log_event("response_poll", elapsed_seconds=round(elapsed, 1),
                           assistant_message_count=_assistant_message_count(page),
                           current_message_index=current_message_index, current_chars=len(current),
                           new_message_seen=new_message_seen, stop_visible=stop_visible,
                           stable_seconds=round(time.monotonic() - stable_since, 1) if stable_since else 0,
                           dom=_dom_diagnostics(page))
                next_diagnostic_at = elapsed + 10.0
            # If ChatGPT explicitly offers to continue a truncated response, click that
            # visible continuation control once per captured text snapshot and keep waiting.
            # Never resubmit the original prompt, which could create duplicate lessons.
            if continue_button is not None and current and current == last_text:
                if stable_since is None:
                    stable_since = time.monotonic()
                stable_for = time.monotonic() - stable_since
                signature = (len(current), continue_button_text.casefold())
                if stable_for >= 2.0 and signature != last_continue_signature:
                    try:
                        continue_button.click(timeout=1500)
                        last_continue_signature = signature
                        stable_since = None
                        _log_event("response_continuation_clicked", elapsed_seconds=round(elapsed, 1),
                                   answer_chars=len(current), button_text=continue_button_text)
                        page.wait_for_timeout(1000)
                        continue
                    except Exception as exc:
                        _log_event("response_continuation_click_failed", exception_type=type(exc).__name__,
                                   message=str(exc)[:200])
            # Require a meaningful quiet window so a long lesson's temporary pause
            # is not mistaken for completion.
            if current and (current != previous_answer or new_message_seen) and current == last_text:
                if stable_since is None:
                    stable_since = time.monotonic()
                stable_for = time.monotonic() - stable_since
                required_stability = 45.0 if stop_visible else _STABLE_SECONDS
                if continue_button is not None:
                    required_stability = max(required_stability, 45.0)
                if continue_button is None and stable_for >= required_stability:
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
                "صفحه یا مرورگر ChatGPT هنگام دریافت پاسخ بسته شد؛ سامانه پس از ۶۰ و ۳۰۰ ثانیه حداکثر دو بار تلاش مجدد می‌کند و در صورت تداوم مشکل، بررسی دستی لازم است.",
            ) from exc
    _log_event("response_timeout", elapsed_seconds=round(time.monotonic() - started, 1),
               last_chars=len(last_text), last_message_count=_assistant_message_count(page),
               last_stop_visible=last_stop_visible, stable_since_set=stable_since is not None)
    raise BrowserAutomationError(
        "chatgpt_response_timeout",
        "در مهلت ۳۰ دقیقه‌ای پاسخ کامل و پایدار از ChatGPT دریافت نشد. بخش دریافت‌شده حفظ شد؛ سامانه پس از ۲ و ۱۰ دقیقه حداکثر دو بار تلاش مجدد می‌کند و در صورت تداوم مشکل، بررسی دستی لازم است.",
        partial_response=last_text or None,
    )


def _lesson_has_terminal_bibliography(text: str) -> bool:
    """Recognize a substantive lesson that ends with a non-empty textual bibliography."""
    source_heading = re.search(
        r"(?im)^\s{0,3}(?:#{1,6}\s*)?(?:\*\*)?"
        r"(?:منابع|منبع|مراجع|منابع و مراجع|مراجع و منابع|فهرست منابع|کتابنامه|"
        r"کتاب‌شناسی|کتاب شناسی|references|sources|bibliography)"
        r"(?:\*\*)?\s*[:：]?\s*$",
        text or "",
    )
    if source_heading is None:
        return False
    source_lines = [
        line.strip() for line in (text or "").splitlines()[len((text or "")[:source_heading.end()].splitlines()):]
        if line.strip() and not re.fullmatch(r"\s*[-*_]{3,}\s*", line)
    ]
    body = (text or "")[:source_heading.start()]
    word_count = len(re.findall(r"[\w]+(?:[-’'][\w]+)*", body, flags=re.UNICODE))
    return bool(source_lines) and word_count >= 500


def _lesson_completion_status(text: str) -> str | None:
    """Read explicit status, or recognize a substantive lesson ending in a bibliography."""
    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    last_line = lines[-1] if lines else ""
    match = re.fullmatch(r"LESSON_STATUS:\s*(COMPLETE|CONTINUE_REQUIRED)\b[^\n]*", last_line, re.IGNORECASE)
    if match:
        return match.group(1).upper()
    # ChatGPT may omit the internal status marker despite finishing the sourced lesson.
    if _lesson_has_terminal_bibliography(text):
        return "COMPLETE"
    return None

def _strip_lesson_status_markers(text: str) -> str:
    """Remove internal continuation markers from saved lesson Markdown."""
    return re.sub(
        r"(?im)^\s*LESSON_STATUS:\s*(?:COMPLETE|CONTINUE_REQUIRED)\b[^\n]*(?:\n|$)",
        "",
        text or "",
    ).rstrip()


def _lesson_word_count(text: str) -> int:
    """Count Unicode words in the merged lesson body, excluding internal status markers."""
    clean = _strip_lesson_status_markers(text)
    return len(re.findall(r"[\w]+(?:[-’'][\w]+)*", clean, flags=re.UNICODE))


def _capture_lesson_until_complete(
    page: Any,
    first_answer: str,
    cancel_event: threading.Event | None = None,
    on_checkpoint: Callable[[str], None] | None = None,
) -> str:
    """Continue a long lesson and durably checkpoint the accumulated text after each part."""
    parts = [first_answer]
    status = _lesson_completion_status(first_answer)
    continuation_count = 0

    def checkpoint() -> None:
        combined = "\n\n".join(_strip_lesson_status_markers(part) for part in parts)
        if on_checkpoint is None or not combined.strip():
            return
        try:
            on_checkpoint(combined)
        except Exception as exc:
            raise BrowserAutomationError(
                "lesson_checkpoint_failed",
                f"ذخیرهٔ مرحله‌ای درس در پایگاه داده ناموفق بود: {type(exc).__name__}",
                partial_response=combined,
            ) from exc

    checkpoint()
    while status != "COMPLETE" or (
        _lesson_word_count("\n\n".join(_strip_lesson_status_markers(part) for part in parts))
        < _MIN_COMPLETE_LESSON_WORDS
        and not _lesson_has_terminal_bibliography(
            "\n\n".join(_strip_lesson_status_markers(part) for part in parts)
        )
    ):
        if cancel_event is not None and cancel_event.is_set():
            raise BrowserAutomationError(
                "run_cancelled",
                "اجرای دریافت پاسخ به درخواست کاربر متوقف شد.",
                partial_response="\n\n".join(_strip_lesson_status_markers(part) for part in parts),
            )
        if continuation_count >= _MAX_LESSON_CONTINUATIONS:
            combined = "\n\n".join(_strip_lesson_status_markers(part) for part in parts)
            _log_event("lesson_continuation_limit", continuation_count=continuation_count,
                       answer_chars=len(combined), completion_status=status)
            raise BrowserAutomationError(
                "lesson_continuation_limit",
                "درس پس از چند مرحله ادامه هنوز نشانگر تکمیل معتبر ندارد؛ متن دریافت‌شده ذخیرهٔ موقت شده و باید از داشبورد ادامه داده شود.",
                partial_response=combined or None,
            )

        continuation_count += 1
        previous = _visible_response(page)
        previous_message_count = _assistant_message_count(page)
        clean_previous = _strip_lesson_status_markers(parts[-1])
        tail = clean_previous[-3500:]
        merged_word_count = _lesson_word_count(
            "\n\n".join(_strip_lesson_status_markers(part) for part in parts)
        )
        if status == "COMPLETE" and merged_word_count < _MIN_COMPLETE_LESSON_WORDS:
            continuation_instruction = (
                "The previous response claimed COMPLETE prematurely, but the merged lesson has only "
                + str(merged_word_count) + " words. This is not acceptable as a complete lesson. "
                "Continue from the exact stopping point and substantially expand the lesson without repeating prior text. "
                "Add the missing instructional depth: explain underlying mechanisms, give step-by-step practical examples, "
                "show expected outputs where relevant, cover edge cases and troubleshooting, discuss security/performance/reliability, "
                "and finish with recap, exercises, and answer key where appropriate. The merged lesson must reach at least "
                + str(_MIN_COMPLETE_LESSON_WORDS) + " words before you may use LESSON_STATUS: COMPLETE. "
            )
        else:
            continuation_instruction = (
                "Continue with the next missing heading, examples, expected outputs, troubleshooting, recap, exercises/answer key, "
                "and sources as applicable. The full merged lesson must contain at least "
                + str(_MIN_COMPLETE_LESSON_WORDS) + " words before it may be marked COMPLETE. "
                "If any planned teaching or the minimum length remains unfinished, use CONTINUE_REQUIRED. "
            )
        continuation_prompt = (
            "Continue the SAME lesson from the exact point where the previous response ended. "
            "Do not restart, repeat earlier paragraphs, or summarize content already written. "
            "Keep the course, topic, language, Markdown structure, technical depth, and terminology consistent. "
            + continuation_instruction
            + "The previous part's final 3500 characters for context follow; treat them as context only, "
            "not as text to repeat:\n\n" + tail + "\n\n"
            "End with one final status line: use 'LESSON_STATUS: CONTINUE_REQUIRED; next_section=last completed heading -> next heading' "
            "if anything remains or the merged lesson is below the minimum, and use exactly 'LESSON_STATUS: COMPLETE' only "
            "when the whole lesson is finished and the merged word count meets the minimum. "
            "Do not omit the status line. Output only new/expanded content, not the earlier lesson."
        )
        _log_event("lesson_continuation_requested", continuation=continuation_count,
                   prior_chars=sum(len(part) for part in parts), completion_status=status)
        _send_prompt(page, continuation_prompt)
        try:
            next_part = _wait_for_complete_answer(
                page, previous, cancel_event, previous_message_count=previous_message_count
            )
        except BrowserAutomationError as exc:
            combined = "\n\n".join(_strip_lesson_status_markers(part) for part in parts)
            if exc.partial_response:
                combined += "\n\n" + _strip_lesson_status_markers(exc.partial_response)
            raise BrowserAutomationError(
                exc.code, str(exc), partial_response=combined or None
            ) from exc
        if not next_part.strip() or next_part.strip() == previous.strip():
            combined = "\n\n".join(_strip_lesson_status_markers(part) for part in parts)
            raise BrowserAutomationError(
                "lesson_continuation_no_progress",
                "ادامهٔ درس محتوای تازه‌ای تولید نکرد؛ متن قبلی حفظ شد و برای ادامه نیاز به بررسی دارد.",
                partial_response=combined or None,
            )
        parts.append(next_part)
        status = _lesson_completion_status(next_part)
        checkpoint()
        _log_event("lesson_continuation_captured", continuation=continuation_count,
                   part_chars=len(next_part), total_chars=sum(len(part) for part in parts),
                   completion_status=status or "missing")
    combined = "\n\n".join(_strip_lesson_status_markers(part) for part in parts)
    if not combined.strip():
        raise BrowserAutomationError("lesson_response_empty", "محتوای درس خالی است.")
    _log_event("lesson_continuation_complete", continuation_count=continuation_count,
               answer_chars=len(combined))
    return combined



def _capture_citation_links(page: Any) -> list[dict[str, str]]:
    """Read external citation hyperlinks from the latest visible ChatGPT answer."""
    try:
        links = page.locator('[data-message-author-role="assistant"]').last.evaluate("""node => {
          const root = node.querySelector('[data-message-content]') || node;
          return Array.from(root.querySelectorAll('a[href]')).map(anchor => ({
            title: (anchor.innerText || anchor.getAttribute('aria-label') || '').trim(),
            url: anchor.href || ''
          })).filter(item => {
            try {
              const url = new URL(item.url);
              return (url.protocol === 'http:' || url.protocol === 'https:') &&
                url.hostname.toLowerCase() !== 'chatgpt.com';
            } catch (_) { return false; }
          });
        }""")
    except Exception as exc:
        _log_event("citation_link_capture_failed", exception_type=type(exc).__name__,
                   message=str(exc)[:200])
        return []
    result: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in links or []:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or "").strip()
        title = " ".join(str(item.get("title") or "").split())
        if not re.match(r"^https?://", url, re.IGNORECASE) or url in seen:
            continue
        seen.add(url)
        result.append({"title": title or url, "url": url})
        if len(result) >= 50:
            break
    _log_event("citation_links_captured", count=len(result))
    return result


def _append_captured_citation_links(answer: str, links: list[dict[str, str]]) -> str:
    """Preserve clickable source URLs that are not exposed in ChatGPT's visible text."""
    existing = set(re.findall(r"https?://[^\s<>\])}]+", answer, re.IGNORECASE))
    additions: list[str] = []
    for item in links:
        url = str(item.get("url") or "").strip()
        if not re.match(r"^https?://", url, re.IGNORECASE) or url in existing:
            continue
        title = str(item.get("title") or "").replace("[", "").replace("]", "")
        title = " ".join(title.split()) or url
        title = title.replace("(", r"\(").replace(")", r"\)")
        additions.append(f"- [{title}]({url})")
        existing.add(url)
    if not additions:
        return answer
    return answer.rstrip() + "\n\nSources (links captured from ChatGPT):\n" + "\n".join(additions)
def _browser_capture(
    prompt: str,
    cancel_event: threading.Event | None = None,
    on_lesson_checkpoint: Callable[[str], None] | None = None,
) -> str:
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
        with _browser_session(sync_playwright, chrome, profile) as (playwright, context):
            try:
                # A persistent Chrome profile can restore old tabs after a crash or
                # interrupted run. Keep exactly one tab per attempt; otherwise every
                # retry can leave another set of tabs visible to the user.
                restored_pages = list(context.pages)
                # Prefer the real ChatGPT page if Chromium exposes both it and
                # an initial blank page during app-window startup.
                page = next(
                    (candidate for candidate in restored_pages
                     if candidate.url and candidate.url != "about:blank"),
                    restored_pages[0] if restored_pages else context.new_page(),
                )
                extra_pages = [candidate for candidate in restored_pages if candidate != page]
                for extra_page in extra_pages:
                    try:
                        extra_page.close(run_before_unload=False)
                    except Exception as close_error:
                        _log_event(
                            "restored_tab_close_failed",
                            exception_type=type(close_error).__name__,
                            message=str(close_error)[:200],
                        )
                if extra_pages:
                    _log_event("restored_extra_tabs_closed", count=len(extra_pages))
                _log_event("page_selected", url=page.url, page_count=len(context.pages))
                if getattr(_BROWSER_SESSION, "instrumented_page", None) is not page:
                    page.on("close", lambda *_: _log_event("page_closed_event", url=page.url))
                    page.on("crash", lambda *_: _log_event("page_crashed_event", url=page.url))
                    page.on("pageerror", lambda error: _log_event("page_javascript_error", message=str(error)[:300]))
                    page.on("console", lambda message: _log_event("page_console_error", message=message.text[:300]) if message.type == "error" else None)
                    page.on("requestfailed", lambda request: _log_event("browser_request_failed", url=request.url.split("?")[0][:300], error=request.failure or "unknown") if any(host in request.url for host in ("chatgpt.com", "openai.com")) else None)
                    _BROWSER_SESSION.instrumented_page = page
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
                # On an empty landing page, classify explicit security/rate-limit gates
                # before submitting. Existing conversations may legitimately discuss these terms.
                if _assistant_message_count(page) == 0:
                    try:
                        landing_text = page.locator("body").inner_text(timeout=1500)
                    except Exception:
                        landing_text = ""
                    gate_error = _provider_gate_error(page.url, landing_text)
                    if gate_error is not None:
                        _log_event("provider_gate_detected", error_code=gate_error.code, url=page.url)
                        raise gate_error
                # Guest mode is an explicit supported attempt: try the visible composer
                # instead of requiring sign-in before submitting. ChatGPT may still impose
                # an account, rate-limit, or human-verification gate; never bypass it.
                if _anonymous_landing_visible(page):
                    _log_event(
                        "guest_mode_attempt",
                        url=page.url,
                        explanation="Anonymous ChatGPT landing detected; attempting normal visible-UI submission.",
                        dom=_dom_diagnostics(page),
                    )
                if "login" in page.url.casefold() or "auth.openai.com" in page.url.casefold():
                    _log_event("guest_auth_gate", url=page.url)
                    raise BrowserAutomationError(
                        "guest_send_unavailable",
                        "ChatGPT برای این درخواست به صفحهٔ ورود هدایت کرد؛ ارسال در حالت مهمان در این وضعیت در دسترس نیست. هیچ ورود یا محدودیت امنیتی دور زده نشد.",
                    )
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
                if "Write educational lesson content in Markdown" in prompt:
                    answer = _capture_lesson_until_complete(page, answer, cancel_event, on_lesson_checkpoint)
                citation_links = _capture_citation_links(page)
                answer = _append_captured_citation_links(answer, citation_links)
                if on_lesson_checkpoint and citation_links:
                    on_lesson_checkpoint(answer)
                _log_event("capture_succeeded", elapsed_seconds=round(time.monotonic() - capture_started, 1),
                           answer_chars=len(answer))
                return answer
            finally:
                if not getattr(_BROWSER_SESSION, "reuse_enabled", False):
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


_AUTO_RETRY_INITIAL_DELAY_SECONDS = 5
_AUTO_RETRY_MAX_DELAY_SECONDS = 120
_AUTO_RETRY_MAX_ATTEMPTS = 5


def _capture_with_automatic_retry(
    prompt: str,
    cancel_event: threading.Event,
    on_lesson_checkpoint: Callable[[str], None] | None = None,
    *,
    run_id: int,
    phase: str,
) -> str:
    """Retry visible Chrome/ChatGPT capture briefly, then let the worker schedule or request manual recovery."""
    attempt = 0
    while attempt < _AUTO_RETRY_MAX_ATTEMPTS:
        if cancel_event.is_set() or service.get_learning_control()["paused"]:
            raise BrowserAutomationError("run_cancelled", "یادگیری به درخواست کاربر متوقف یا مکث شد.")
        try:
            return _browser_capture(prompt, cancel_event, on_lesson_checkpoint)
        except BrowserAutomationError as exc:
            if exc.code in {"run_cancelled", "chatgpt_browser_closed"} or cancel_event.is_set() or service.get_learning_control()["paused"]:
                raise
            # Provider rate limits and long response timeouts explicitly ask for
            # a later retry; do not hammer the same session with short backoffs.
            if exc.code in {"provider_rate_limited", "chatgpt_response_timeout", "chatgpt_page_closed"}:
                attempt = _AUTO_RETRY_MAX_ATTEMPTS - 1
            error_code = exc.code
            error_message = str(exc)
            partial_response = exc.partial_response
        except Exception as exc:
            if cancel_event.is_set() or service.get_learning_control()["paused"]:
                raise BrowserAutomationError("run_cancelled", "یادگیری به درخواست کاربر متوقف یا مکث شد.") from exc
            error_code = "automatic_retry_unexpected_error"
            error_message = f"{type(exc).__name__}: {exc}"
            partial_response = None

        attempt += 1
        if attempt >= _AUTO_RETRY_MAX_ATTEMPTS:
            _log_event(
                "automatic_browser_retry_exhausted",
                run_id=run_id,
                phase=phase,
                attempts=attempt,
                error_code=error_code,
                message=error_message[:500],
            )
            raise BrowserAutomationError(
                error_code,
                f"پس از {attempt} تلاش خودکار (تلاش سریع داخلی)، دریافت پاسخ متوقف شد. آخرین خطا: {error_message[:500]}",
                partial_response=partial_response,
            )

        delay = min(
            _AUTO_RETRY_MAX_DELAY_SECONDS,
            _AUTO_RETRY_INITIAL_DELAY_SECONDS * (2 ** min(attempt - 1, 5)),
        )
        _log_event(
            "automatic_browser_retry_scheduled",
            run_id=run_id,
            phase=phase,
            attempt=attempt,
            max_attempts=_AUTO_RETRY_MAX_ATTEMPTS,
            delay_seconds=delay,
            error_code=error_code,
            message=error_message[:500],
        )
        # Event.wait makes a manual pause/cancel interrupt the backoff immediately.
        if cancel_event.wait(delay):
            raise BrowserAutomationError("run_cancelled", "یادگیری به درخواست کاربر متوقف یا مکث شد.")
        if service.get_learning_control()["paused"]:
            raise BrowserAutomationError("run_cancelled", "یادگیری به درخواست کاربر متوقف یا مکث شد.")


def _continue_lessons(
    course_id: int,
    cancel_event: threading.Event,
    selected_topic_ids: list[int] | None = None,
    force_retry: bool = False,
) -> None:
    """Capture lessons sequentially, keeping every browser prompt bounded to one topic."""
    global _ACTIVE_RUN_ID
    # Never approve topics as a side effect of background learning. Approval is a
    # deliberate dashboard action so users can review the outline before lesson capture.
    pending_ids = list(dict.fromkeys(int(item) for item in selected_topic_ids or []))
    while True:
        _assert_worker_lease()
        if service.get_learning_control()["paused"] or cancel_event.is_set():
            _log_event("lesson_followup_paused", course_id=course_id)
            return
        course = service.get_course(course_id)
        topics = course.get("topics", [])
        if selected_topic_ids is None:
            eligible = [
                int(topic["id"]) for topic in topics
                if topic.get("approval_status") == "approved"
                and topic.get("processing_status") != "paused"
                and int(topic.get("lesson_content_ready_count") or 0) == 0
            ]
            if not eligible:
                return
            topic_id = eligible[0]
        else:
            # Explicit selections must not silently expand to every approved topic.
            eligible_by_id = {int(topic["id"]): topic for topic in topics}
            while pending_ids:
                candidate_id = pending_ids.pop(0)
                topic = eligible_by_id.get(candidate_id)
                if topic is None or topic.get("approval_status") != "approved" or topic.get("processing_status") == "paused":
                    continue
                if not force_retry and int(topic.get("lesson_content_ready_count") or 0) > 0:
                    continue
                topic_id = candidate_id
                break
            else:
                return
        topic_ids = [topic_id]
        request_key = (
            f"playwright-lessons-{course_id}-{topic_id}-"
            f"{'retry' if force_retry else 'selected'}-{int(time.time())}"
        )
        _assert_worker_lease()
        run = service.create_run(
            course_id, run_type="lesson", topic_ids=topic_ids,
            selection_mode="selected", request_key=request_key, force_retry=force_retry,
        )
        run_id = int(run["id"])
        service.start_run(run_id, **_worker_lease_kwargs())
        with _LOCK:
            previous_active_run_id = _ACTIVE_RUN_ID
            _ACTIVE_RUN_ID = run_id
            _CANCEL_EVENTS[run_id] = cancel_event
        try:
            def save_followup_partial(text: str) -> dict[str, Any]:
                _assert_worker_lease()
                return service.save_run_partial_response(run_id, text, **_worker_lease_kwargs())

            answer = _capture_with_automatic_retry(
                run["prompt_text"], cancel_event,
                save_followup_partial,
                run_id=run_id, phase="lesson_followup",
            )
            _assert_worker_lease()
            result = service.complete_run(run_id, answer, **_worker_lease_kwargs())
        except BrowserAutomationError as exc:
            if _WORKER_LEASE_EPOCH is not None and not service.owns_worker_lease(_WORKER_OWNER, _WORKER_LEASE_EPOCH):
                return
            if cancel_event.is_set() or service.get_learning_control()["paused"]:
                service.requeue_run_for_resume(run_id, "manual_stop_during_lesson_followup", **_worker_lease_kwargs())
                return
            service.mark_run_needs_user_action(run_id, exc.code, str(exc), exc.partial_response, **_worker_lease_kwargs())
            return
        except Exception as exc:
            if _WORKER_LEASE_EPOCH is not None and not service.owns_worker_lease(_WORKER_OWNER, _WORKER_LEASE_EPOCH):
                return
            if cancel_event.is_set() or service.get_learning_control()["paused"]:
                service.requeue_run_for_resume(run_id, "manual_stop_during_lesson_followup")
                return
            service.mark_run_needs_user_action(
                run_id, "lesson_batch_failed", f"خطا در پردازش سرفصل: {str(exc)[:700]}",
                **_worker_lease_kwargs(),
            )
            return
        finally:
            with _LOCK:
                _CANCEL_EVENTS.pop(run_id, None)
                if _ACTIVE_RUN_ID == run_id:
                    _ACTIVE_RUN_ID = previous_active_run_id
        if result.get("status") != "succeeded":
            return


def _schedule_transient_retry(
    run_id: int, error: BrowserAutomationError
) -> dict[str, Any] | None:
    """Schedule a bounded delayed retry only for explicitly transient provider/browser failures."""
    _assert_worker_lease()
    delays = _AUTOMATIC_RETRY_DELAYS_SECONDS.get(error.code)
    if not delays:
        return None
    run = service.get_run(run_id)
    retry_count = max(0, int(run.get("automatic_retry_count") or 0))
    if retry_count >= len(delays):
        return None
    partial = error.partial_response
    if partial is not None and not partial.strip():
        partial = None
    return service.schedule_run_retry(
        run_id,
        error.code,
        str(error)[:1000],
        delay_seconds=delays[retry_count],
        response_text=partial,
        max_attempts=len(delays),
        **_worker_lease_kwargs(),
    )


def _worker(
    run_id: int, course_id: int, prompt: str, cancel_event: threading.Event,
    continue_after: bool = False, followup_topic_ids: list[int] | None = None,
    force_retry: bool = False,
) -> None:
    global _ACTIVE, _ACTIVE_RUN_ID, _ACTIVE_COURSE_ID
    worker_started = time.monotonic()
    run_type = service.get_run(run_id).get("run_type")
    lease_epoch = _WORKER_LEASE_EPOCH
    lease_stop = threading.Event()
    lease_thread: threading.Thread | None = None
    _log_event("worker_started", run_id=run_id, course_id=course_id, run_type=run_type, prompt_chars=len(prompt))
    _BROWSER_SESSION.reuse_enabled = True
    try:
        if lease_epoch is not None:
            lease_thread = threading.Thread(
                target=_maintain_worker_lease,
                args=(cancel_event, lease_stop, lease_epoch),
                name=f"ai-learning-lease-{run_id}",
                daemon=True,
            )
            lease_thread.start()

        def checkpoint_partial(text: str) -> dict[str, Any]:
            _assert_worker_lease(lease_epoch)
            return service.save_run_partial_response(run_id, text, **_worker_lease_kwargs(lease_epoch))

        checkpoint = checkpoint_partial if run_type == "lesson" else None
        answer = _capture_with_automatic_retry(
            prompt, cancel_event, checkpoint, run_id=run_id, phase="primary_run"
        )
        if cancel_event.is_set():
            raise BrowserAutomationError(
                "run_cancelled", "اجرای یادگیری به درخواست توقف متوقف شد."
            )
        _assert_worker_lease(lease_epoch)
        _log_event("database_completion_started", run_id=run_id, answer_chars=len(answer))
        result = service.complete_run(run_id, answer, **_worker_lease_kwargs(lease_epoch))
        _log_event("database_completion_finished", run_id=run_id, status=result.get("status"),
                   elapsed_seconds=round(time.monotonic() - worker_started, 1))
        if result.get("status") == "succeeded" and run_type == "outline":
            try:
                _continue_lessons(course_id, cancel_event, followup_topic_ids, force_retry)
            except Exception as exc:
                # The outline remains safely saved even if lesson follow-up fails.
                print(f"AI-learning lesson follow-up failed for course {course_id}: {exc}", flush=True)
    except BrowserAutomationError as exc:
        _log_event("worker_browser_error", run_id=run_id, error_code=exc.code, message=str(exc)[:500],
                   elapsed_seconds=round(time.monotonic() - worker_started, 1))
        try:
            if lease_epoch is not None and not service.owns_worker_lease(_WORKER_OWNER, lease_epoch):
                _log_event("worker_result_discarded_after_lease_loss", run_id=run_id, lease_epoch=lease_epoch)
                return
            if cancel_event.is_set() or service.get_learning_control()["paused"]:
                service.requeue_run_for_resume(run_id, "manual_stop_during_primary_run", **_worker_lease_kwargs(lease_epoch))
            else:
                scheduled = _schedule_transient_retry(run_id, exc)
                if scheduled is not None:
                    _log_event(
                        "worker_transient_retry_scheduled",
                        run_id=run_id,
                        error_code=exc.code,
                        retry_count=scheduled.get("automatic_retry_count"),
                        next_retry_at=scheduled.get("next_retry_at"),
                    )
                else:
                    service.mark_run_needs_user_action(
                        run_id,
                        exc.code,
                        str(exc),
                        exc.partial_response if exc.partial_response and exc.partial_response.strip() else None,
                        **_worker_lease_kwargs(lease_epoch),
                    )
        except Exception as mark_exc:
            _log_event(
                "worker_failure_state_update_failed",
                run_id=run_id,
                exception_type=type(mark_exc).__name__,
                message=str(mark_exc)[:300],
            )
            print(f"Could not update AI-learning run {run_id}: {mark_exc}", flush=True)
    except Exception as exc:
        _log_event("worker_unexpected_error", run_id=run_id, exception_type=type(exc).__name__,
                   message=str(exc)[:500], elapsed_seconds=round(time.monotonic() - worker_started, 1))
        try:
            if lease_epoch is not None and not service.owns_worker_lease(_WORKER_OWNER, lease_epoch):
                _log_event("worker_result_discarded_after_lease_loss", run_id=run_id, lease_epoch=lease_epoch)
                return
            if cancel_event.is_set() or service.get_learning_control()["paused"]:
                service.requeue_run_for_resume(run_id, "manual_stop_during_primary_run")
            else:
                service.mark_run_needs_user_action(
                    run_id, "playwright_unexpected_error",
                    f"خطای غیرمنتظره در دریافت پاسخ: {str(exc)[:700]}",
                    **_worker_lease_kwargs(lease_epoch),
                )
        except Exception as mark_exc:
            print(f"Could not update AI-learning run {run_id}: {mark_exc}", flush=True)
    finally:
        _close_browser_session()
        _BROWSER_SESSION.reuse_enabled = False
        lease_stop.set()
        if lease_thread is not None:
            lease_thread.join(timeout=max(1.0, _WORKER_LEASE_HEARTBEAT_SECONDS + 1.0))
        _log_event("worker_finished", run_id=run_id, elapsed_seconds=round(time.monotonic() - worker_started, 1))
        with _LOCK:
            _ACTIVE = False
            _ACTIVE_RUN_ID = None
            _ACTIVE_COURSE_ID = None
            _CANCEL_EVENTS.pop(run_id, None)
        # Drain only requests that were explicitly persisted by a user/API call.
        # Do not auto-create new lessons during startup recovery.
        try:
            _dispatch_next_persisted_run()
        except Exception as exc:
            _log_event("persisted_queue_dispatch_failed", exception_type=type(exc).__name__,
                       message=str(exc)[:300])


def _start_worker_thread(
    thread: threading.Thread,
    run_id: int,
    cancel_event: threading.Event,
) -> None:
    """Start a claimed worker or restore its durable run to a recoverable state."""
    global _ACTIVE, _ACTIVE_RUN_ID, _ACTIVE_COURSE_ID
    try:
        thread.start()
    except Exception as exc:
        cancel_event.set()
        _ACTIVE = False
        _ACTIVE_RUN_ID = None
        _ACTIVE_COURSE_ID = None
        _CANCEL_EVENTS.pop(run_id, None)
        durable_status = "recovery_failed"
        try:
            recovered = service.requeue_run_for_resume(run_id, "worker_thread_start_failed", **_worker_lease_kwargs())
            durable_status = str(recovered.get("status") or "unknown")
        except Exception as recovery_exc:
            _log_event(
                "worker_start_requeue_failed",
                run_id=run_id,
                exception_type=type(recovery_exc).__name__,
                message=str(recovery_exc)[:300],
            )
        _log_event(
            "worker_thread_start_failed",
            run_id=run_id,
            exception_type=type(exc).__name__,
            message=str(exc)[:300],
            durable_status=durable_status,
        )
        _release_worker_lease()
        raise BrowserAutomationError(
            "worker_thread_start_failed",
            "راه‌اندازی worker یادگیری ناموفق بود؛ اجرا برای بازیابی در صف نگه داشته شد. کمی بعد وضعیت را بازخوانی یا ادامه دهید.",
        ) from exc


def _dispatch_next_persisted_run() -> dict[str, Any]:
    """Claim the process-wide browser lease before dispatching one durable queued run."""
    global _ACTIVE, _ACTIVE_RUN_ID, _ACTIVE_COURSE_ID, _WORKER_LEASE_EPOCH
    with _LOCK:
        if _ACTIVE:
            return {"status": "already_active", "active": True, "run_id": _ACTIVE_RUN_ID}
        if _SHUTTING_DOWN:
            _release_worker_lease()
            return {"status": "shutting_down", "active": False}
        control = service.get_learning_control()
        if control["paused"]:
            _release_worker_lease()
            return {"status": "paused", "active": False}
        if control.get("retry_pending", False):
            _release_worker_lease()
            return {
                "status": "retry_scheduled",
                "active": False,
                "next_retry_at": control["retry_after"],
                "message": "صف یادگیری تا زمان تلاش مجدد خودکار در مکث موقت است.",
            }

        # SQLite serializes this claim across independent application processes.
        # The lease covers the shared Chrome profile as well as the durable run.
        epoch = service.claim_worker_lease(
            _WORKER_OWNER, lease_seconds=_WORKER_LEASE_SECONDS
        )
        if epoch is None:
            return {
                "status": "worker_busy",
                "active": False,
                "message": "یک پردازهٔ دیگر مالک worker مرورگر است؛ کار در صف پایدار باقی ماند.",
            }
        _WORKER_LEASE_EPOCH = epoch
        try:
            selected = service.get_oldest_queued_run()
            if selected is None:
                _release_worker_lease()
                return {"status": "idle", "active": False}
            run_id = int(selected["id"])
            course_id = int(selected["course_id"])
            run = service.start_run(run_id, **_worker_lease_kwargs(epoch))
            cancel_event = threading.Event()
            _ACTIVE = True
            _ACTIVE_RUN_ID = run_id
            _ACTIVE_COURSE_ID = course_id
            _CANCEL_EVENTS[run_id] = cancel_event
            thread = threading.Thread(
                target=_worker,
                args=(run_id, course_id, str(run["prompt_text"]), cancel_event),
                name=f"ai-learning-durable-run-{run_id}",
                daemon=True,
            )
            _start_worker_thread(thread, run_id, cancel_event)
            _log_event(
                "persisted_queue_run_dispatched",
                run_id=run_id,
                course_id=course_id,
                run_type=run.get("run_type"),
                recovered_status=selected.get("status"),
                lease_epoch=epoch,
            )
            return {"status": "running", "active": True, "run_id": run_id, "course_id": course_id}
        except Exception:
            _release_worker_lease()
            raise


def stop_browser_automation() -> dict[str, Any]:
    """Signal active workers and give Playwright time to close before process shutdown."""
    global _SHUTTING_DOWN
    with _LOCK:
        _SHUTTING_DOWN = True
        active_run_id = _ACTIVE_RUN_ID
        events = list(_CANCEL_EVENTS.values())
        for event in events:
            event.set()

    # Workers are daemon threads, so without a bounded wait the Python process can
    # exit while the Playwright Node driver still writes to its pipe (EPIPE).
    wait_started = time.monotonic()
    deadline = wait_started + 12.0
    while time.monotonic() < deadline:
        with _LOCK:
            still_active = _ACTIVE
        if not still_active:
            break
        time.sleep(0.1)
    with _LOCK:
        worker_stopped = not _ACTIVE
    _log_event(
        "browser_automation_shutdown",
        active_run_id=active_run_id,
        signalled_workers=len(events),
        worker_stopped=worker_stopped,
        wait_seconds=round(time.monotonic() - wait_started, 2),
    )
    return {
        "status": "shutting_down",
        "active_run_id": active_run_id,
        "signalled_workers": len(events),
        "worker_stopped": worker_stopped,
    }


def recover_pending_runs() -> dict[str, Any]:
    """Recover interrupted runs only after atomically acquiring the shared browser lease."""
    global _SHUTTING_DOWN, _WORKER_LEASE_EPOCH
    from .db import initialize_store

    initialize_store()
    _SHUTTING_DOWN = False
    if service.get_learning_control()["paused"]:
        return {"status": "paused", "active": False, "message": "Global learning pause is active."}
    with _LOCK:
        if _ACTIVE:
            return {
                "status": "already_active",
                "active": True,
                "run_id": _ACTIVE_RUN_ID,
                "message": "An in-process learning worker is already active.",
            }
        epoch = service.claim_worker_lease(_WORKER_OWNER, lease_seconds=_WORKER_LEASE_SECONDS)
        if epoch is None:
            control = service.get_learning_control()
            return {
                "status": "paused" if control["paused"] else "worker_busy",
                "active": False,
                "message": "Another process owns the browser worker lease.",
            }
        _WORKER_LEASE_EPOCH = epoch
        try:
            # A live worker in another process keeps this lease renewed. Reaching
            # this point therefore means every running row is stale/recoverable.
            for run_id in service.list_run_ids_by_status("running"):
                service.requeue_run_for_resume(int(run_id), "startup_interrupted_run", **_worker_lease_kwargs(epoch))
        except Exception:
            _release_worker_lease()
            raise
    result = _dispatch_next_persisted_run()
    _log_event("startup_queue_recovery", status=result.get("status"), run_id=result.get("run_id"))
    return result


def recover_orphaned_runs() -> dict[str, Any]:
    """Recover orphaned runs only after the cross-process lease is available."""
    global _WORKER_LEASE_EPOCH
    from .db import initialize_store

    if _SHUTTING_DOWN:
        return {"status": "shutting_down", "active": False}
    initialize_store()
    if service.get_learning_control()["paused"]:
        return {"status": "paused", "active": False}
    with _LOCK:
        if _SHUTTING_DOWN:
            return {"status": "shutting_down", "active": False}
        if _ACTIVE:
            return {
                "status": "already_active",
                "active": True,
                "run_id": _ACTIVE_RUN_ID,
            }
        epoch = service.claim_worker_lease(_WORKER_OWNER, lease_seconds=_WORKER_LEASE_SECONDS)
        if epoch is None:
            control = service.get_learning_control()
            return {
                "status": "paused" if control["paused"] else "worker_busy",
                "active": False,
            }
        _WORKER_LEASE_EPOCH = epoch
        try:
            orphaned_ids = service.list_run_ids_by_status("running")
            for run_id in orphaned_ids:
                service.requeue_run_for_resume(int(run_id), "worker_lost_during_process_lifetime", **_worker_lease_kwargs(epoch))
        except Exception:
            _release_worker_lease()
            raise
    result = _dispatch_next_persisted_run()
    _log_event(
        "orphaned_run_watchdog_recovery",
        status=result.get("status"),
        run_id=result.get("run_id"),
        requeued_count=len(orphaned_ids),
    )
    return result


def start_outline(course_id: int, request_key: str) -> dict[str, Any]:
    """Persist an outline request, then dispatch it under the shared browser lease."""
    control = service.get_learning_control()
    if control["paused"]:
        raise ValueError("مکث سراسری فعال است؛ برای شروع یا ادامه ابتدا «ادامه / بازیابی» را بزنید.")
    if control.get("retry_pending", False):
        raise ValueError(
            "به‌دلیل خطای موقت ChatGPT، صف تا " + str(control["retry_after"])
            + " در مکث خودکار است؛ تلاش بعدی بدون نیاز به بازکردن دستی Chrome انجام می‌شود."
        )
    with _LOCK:
        if _ACTIVE:
            raise ValueError("یک کار مرورگر در حال اجراست؛ تا پایان آن صبر کنید.")
        run = service.create_run(
            course_id, run_type="outline", topic_ids=[], selection_mode="single",
            request_key=request_key,
        )
        if run["status"] not in {"queued", "running"}:
            raise ValueError("این درخواست قبلاً اجرا شده است؛ برای تلاش دوباره بازخوانی را دوباره بزنید.")

    # An idempotent request may already be running in another process. The
    # watchdog only reclaims it if its durable lease has expired.
    dispatch = (
        recover_orphaned_runs()
        if run["status"] == "running"
        else _dispatch_next_persisted_run()
    )
    started = dispatch.get("status") == "running" and int(dispatch.get("run_id") or 0) == int(run["id"])
    status = "running" if started or run["status"] == "running" else "queued"
    return {
        "run_id": int(run["id"]),
        "status": status,
        "provider": "ChatGPT visible UI",
        "message": (
            "اجرای طرح درس آغاز شد."
            if started
            else "درخواست در صف پایدار ثبت شد و پس از آزادشدن worker مرورگر اجرا می‌شود."
        ),
    }


def start_lesson_run(course_id: int, topic_ids: list[int], request_key: str, force_retry: bool = False) -> dict[str, Any]:
    """Persist every selected topic as its own FIFO run before dispatching browser work."""
    global _ACTIVE, _ACTIVE_RUN_ID, _ACTIVE_COURSE_ID
    globally_paused = service.get_learning_control()["paused"]
    requested_ids = list(dict.fromkeys(int(item) for item in topic_ids))
    if not requested_ids:
        raise ValueError("حداقل یک سرفصل برای دریافت درس انتخاب کنید.")
    with _LOCK:
        course = service.get_course(course_id)
        course_topics = {int(topic["id"]): topic for topic in course.get("topics", [])}
        missing = [item for item in requested_ids if item not in course_topics]
        if missing:
            raise ValueError("یک یا چند سرفصل انتخاب‌شده در این دوره وجود ندارد؛ فهرست را بازخوانی کنید.")
        paused_ids = [
            item for item in requested_ids
            if course_topics[item].get("processing_status") == "paused"
        ]
        if paused_ids:
            raise ValueError("ابتدا مکث سرفصل‌های انتخاب‌شده را بردارید.")
        unapproved = [
            item for item in requested_ids
            if course_topics[item].get("approval_status") != "approved"
        ]
        if unapproved:
            raise ValueError("فقط سرفصل‌های تأییدشده قابل دریافت درس هستند.")
        if globally_paused:
            if not force_retry:
                raise ValueError("مکث سراسری فعال است؛ ابتدا ادامه / بازیابی را بزنید.")
            # An explicit retry is a user-directed recovery action. Clear a stale/global
            # pause only after validating the requested course and approved topics.
            service.set_learning_paused(False, "شروع مجدد صریح دریافت درس توسط کاربر")
            _log_event(
                "global_pause_cleared_for_explicit_lesson_retry",
                course_id=course_id,
                topic_ids=requested_ids,
            )
        selected_ids = [
            item for item in requested_ids
            if force_retry or int(course_topics[item].get("lesson_content_ready_count") or 0) == 0
        ]
        if not selected_ids:
            raise ValueError("تمام سرفصل‌های انتخاب‌شده از قبل درس دارند؛ برای ساخت نسخه جدید، تلاش مجدد را انتخاب کنید.")

        # A durable run is created for every selected topic before the first browser
        # request starts. A process restart can therefore recover the whole selection;
        # no remaining topic exists only in a worker's in-memory follow-up list.
        queued_runs: list[int] = []
        for topic_id in selected_ids:
            topic_request_key = f"{request_key[:75]}-topic-{topic_id}"
            queued = service.create_run(
                course_id, run_type="lesson", topic_ids=[topic_id],
                selection_mode="selected", request_key=topic_request_key,
                force_retry=force_retry,
            )
            if queued["status"] != "queued":
                raise ValueError(
                    "برای یکی از سرفصل‌ها درخواست تکراری یا در حال اجرا وجود دارد؛ سوابق اجرا را بازخوانی کنید."
                )
            queued_runs.append(int(queued["id"]))

        if _ACTIVE:
            _log_event(
                "lesson_batch_queued_while_active",
                active_run_id=_ACTIVE_RUN_ID,
                course_id=course_id,
                queued_run_ids=queued_runs,
                topic_ids=selected_ids,
            )
            return {
                "run_id": queued_runs[0],
                "status": "queued",
                "course_id": course_id,
                "topic_ids": selected_ids,
                "queued_topic_count": len(selected_ids),
                "provider": "ChatGPT visible UI",
                "message": f"مرورگر مشغول است؛ درخواست {len(selected_ids)} سرفصل به‌صورت مستقل در صف پایدار ذخیره شد و پس از پایان اجرای فعلی به‌ترتیب اجرا می‌شود.",
            }

    # Dispatch outside _LOCK: the dispatcher acquires the same lock and enforces
    # global FIFO ordering, including work submitted by earlier requests.
    first_run_id = queued_runs[0]
    dispatch = _dispatch_next_persisted_run()
    started = dispatch.get("status") == "running" and int(dispatch.get("run_id") or 0) == first_run_id
    retry_after = dispatch.get("next_retry_at")
    if dispatch.get("status") == "retry_scheduled":
        message = (
            f"درخواست {len(selected_ids)} سرفصل در صف پایدار ثبت شد؛ به‌دلیل خطای موقت ChatGPT "
            f"تا {retry_after} هیچ درخواست دیگری ارسال نمی‌شود و سپس بازیابی خودکار آغاز خواهد شد."
        )
    elif started:
        message = (
            f"دریافت درس برای {len(selected_ids)} سرفصل در صف پایدار ثبت شد؛ هر سرفصل اجرای مستقل دارد "
            "و پس از هر اجرا، مورد بعدی از صف بازیابی می‌شود."
        )
    else:
        message = (
            f"درخواست {len(selected_ids)} سرفصل در صف پایدار ثبت شد و پس از اجرای کارهای قدیمی‌تر آغاز می‌شود."
        )
    return {
        "run_id": first_run_id,
        "status": "running" if started else "queued",
        "course_id": course_id,
        "topic_ids": selected_ids,
        "queued_topic_count": len(selected_ids),
        "next_retry_at": retry_after,
        "provider": "ChatGPT visible UI",
        "message": message,
    }


def cancel_run(run_id: int) -> dict[str, Any]:
    """Signal the local browser worker and persist cancellation of its run."""
    with _LOCK:
        event = _CANCEL_EVENTS.get(run_id)
        if event is not None:
            event.set()
    return service.cancel_run(run_id)


def set_topic_paused(topic_id: int, paused: bool) -> dict[str, Any]:
    """Pause a topic without terminally cancelling its active durable lesson run."""
    result = service.set_topic_paused(topic_id, paused)
    if paused:
        with _LOCK:
            active_run_id = _ACTIVE_RUN_ID
            active_cancel_event = _CANCEL_EVENTS.get(active_run_id) if active_run_id is not None else None
        if active_run_id is not None:
            try:
                run = service.get_run(active_run_id)
                if any(int(item["topic_id"]) == topic_id for item in run.get("topics", [])):
                    # Interrupt the browser wait, but keep the run recoverable in SQLite.
                    if active_cancel_event is not None:
                        active_cancel_event.set()
                    queued = service.requeue_run_for_resume(active_run_id, "topic_paused", **_worker_lease_kwargs())
                    _log_event(
                        "topic_run_paused",
                        topic_id=topic_id,
                        run_id=active_run_id,
                        run_status=queued.get("status"),
                    )
            except Exception as exc:
                _log_event("topic_pause_requeue_failed", topic_id=topic_id, error=str(exc)[:300])
                raise
    else:
        with _LOCK:
            worker_active = _ACTIVE
        if not worker_active:
            # A paused queued run remains durable but is skipped by the dispatcher.
            # Unpausing makes it eligible again, so wake the FIFO dispatcher now.
            try:
                dispatch = _dispatch_next_persisted_run()
                _log_event(
                    "topic_unpaused_queue_dispatch",
                    topic_id=topic_id,
                    dispatch_status=dispatch.get("status"),
                    run_id=dispatch.get("run_id"),
                )
            except Exception as exc:
                _log_event(
                    "topic_unpaused_dispatch_failed",
                    topic_id=topic_id,
                    exception_type=type(exc).__name__,
                    message=str(exc)[:300],
                )
                raise
    return result


def reset_learning_data() -> dict[str, Any]:
    """Refuse destructive reset while any browser worker can still write to the store."""
    with _LOCK:
        if _ACTIVE:
            raise ValueError(
                "پاک‌سازی امن نیست؛ ابتدا «مکث همه» را بزنید، اجرای فعال را لغو کنید "
                "و صبر کنید تا وضعیت اجرا از در حال اجرا خارج شود."
            )
    return service.reset_learning_data()


def pause_learning() -> dict[str, Any]:
    """Pause all ChatGPT outline and lesson capture, preserving completed database work."""
    state = service.set_learning_paused(True, "توقف سراسری توسط کاربر")
    with _LOCK:
        active_run_id = _ACTIVE_RUN_ID
        active_event = _CANCEL_EVENTS.get(active_run_id) if active_run_id is not None else None
        if active_event is not None:
            active_event.set()
    # The active browser may belong to another application process. Persist the
    # pause transition for every running record; the remote heartbeat observes it
    # and cooperatively stops the provider interaction.
    try:
        for run_id in service.list_run_ids_by_status("running"):
            service.requeue_run_for_resume(int(run_id), "manual_global_pause")
    except Exception as exc:
        _log_event(
            "pause_run_requeue_failed",
            run_id=active_run_id,
            exception_type=type(exc).__name__,
            message=str(exc)[:300],
        )
        raise
    _log_event("learning_paused", active_run_id=active_run_id, active_course_id=_ACTIVE_COURSE_ID)
    return {**state, "active": _ACTIVE, "active_run_id": active_run_id}


def resume_learning(course_id: int | None = None) -> dict[str, Any]:
    """Resume an active worker, recover a persisted in-flight run, or continue remaining lesson batches."""
    global _ACTIVE, _ACTIVE_RUN_ID, _ACTIVE_COURSE_ID, _WORKER_LEASE_EPOCH
    service.set_learning_paused(False)
    with _LOCK:
        if _ACTIVE:
            return {"status": "resumed", "active": True, "run_id": _ACTIVE_RUN_ID,
                    "course_id": _ACTIVE_COURSE_ID, "message": "فرایند فعال ادامه پیدا می‌کند."}
        control = service.get_learning_control()
        if control.get("retry_pending", False):
            return {
                "status": "retry_scheduled",
                "active": False,
                "next_retry_at": control["retry_after"],
                "message": "صف یادگیری تا زمان تلاش مجدد خودکار در مکث موقت است؛ کارهای باقی‌مانده پس از آن ادامه پیدا می‌کنند.",
            }

        epoch = service.claim_worker_lease(_WORKER_OWNER, lease_seconds=_WORKER_LEASE_SECONDS)
        if epoch is None:
            return {
                "status": "worker_busy",
                "active": False,
                "message": "یک پردازهٔ دیگر مالک worker مرورگر است؛ درخواست در صف پایدار باقی می‌ماند.",
            }
        _WORKER_LEASE_EPOCH = epoch
        try:
            # Resume from the durable global queue, not bounded dashboard pages.
            # Otherwise older runs can be stranded after the user exceeds page limits.
            in_flight = service.get_oldest_resumable_run(course_id)
            if in_flight is not None:
                if in_flight.get("status") in {"running", "needs_user_action"}:
                    service.requeue_run_for_resume(
                        int(in_flight["id"]), "manual_resume_recovery", reset_retry_budget=True,
                        **_worker_lease_kwargs(epoch),
                    )
                run = service.start_run(
                    int(in_flight["id"]), reset_retry_budget=True, **_worker_lease_kwargs(epoch)
                )
                run = service.get_run(int(in_flight["id"]))
                run_id = int(run["id"])
                target_course = int(run["course_id"])
    
                # Legacy versions kept selected follow-up topic IDs only in memory.
                # If no durable same-course run remains, materialize the approved pending
                # topics now, before the resumed worker starts. New batches already have
                # queued records and are deliberately not expanded beyond their selection.
                queued_topic_count = 0
                if run.get("run_type") == "lesson" and not service.has_queued_run_for_course(target_course):
                    current_topic_ids = {
                        int(item["topic_id"]) for item in run.get("topics", [])
                    }
                    course = service.get_course(target_course)
                    remaining = [
                        int(topic["id"]) for topic in course.get("topics", [])
                        if int(topic["id"]) not in current_topic_ids
                        and topic.get("approval_status") == "approved"
                        and topic.get("processing_status") != "paused"
                        and int(topic.get("lesson_content_ready_count") or 0) == 0
                    ]
                    batch_key = str(int(time.time()))
                    for topic_id in remaining:
                        request_key = f"playwright-lessons-{target_course}-{topic_id}-resume-{batch_key}"
                        queued = service.create_run(
                            target_course, run_type="lesson", topic_ids=[topic_id],
                            selection_mode="selected", request_key=request_key,
                        )
                        if queued["status"] != "queued":
                            raise ValueError("یکی از سرفصل‌های بازیابی از قبل در صف یا در حال اجراست؛ وضعیت را بازخوانی کنید.")
                        queued_topic_count += 1
    
                _ACTIVE = True
                _ACTIVE_RUN_ID = run_id
                _ACTIVE_COURSE_ID = target_course
                cancel_event = threading.Event()
                _CANCEL_EVENTS[run_id] = cancel_event
                thread = threading.Thread(
                    target=_worker, args=(run_id, target_course, str(run["prompt_text"]), cancel_event),
                    name=f"ai-learning-resume-{run_id}", daemon=True,
                )
                _start_worker_thread(thread, run_id, cancel_event)
                _log_event(
                    "learning_run_recovered",
                    run_id=run_id,
                    course_id=target_course,
                    run_type=run["run_type"],
                    queued_followup_topic_count=queued_topic_count,
                )
                return {
                    "status": "resumed",
                    "active": True,
                    "run_id": run_id,
                    "course_id": target_course,
                    "queued_topic_count": queued_topic_count + (1 if run.get("run_type") == "lesson" else 0),
                    "message": (
                        "اجرای ناتمام درس دوباره آغاز شد و سرفصل‌های تأییدشدهٔ باقی‌مانده در صف پایدار قرار گرفتند."
                        if run.get("run_type") == "lesson"
                        else "اجرای ناتمام طرح درس دوباره آغاز شد؛ پس از بررسی و تأیید سرفصل‌ها می‌توانید دریافت درس را آغاز کنید."
                    ),
                }
    
            # Resolve eligible work in SQLite rather than scanning the dashboard's
            # bounded course pages. This fallback is used only when no durable run exists.
            pending_course_id = service.get_oldest_course_with_pending_approved_topic(course_id)
            if pending_course_id is not None:
                course = service.get_course(pending_course_id)
                cid = int(course["id"])
                eligible = [int(topic["id"]) for topic in course.get("topics", [])
                            if topic.get("approval_status") == "approved"
                            and topic.get("processing_status") != "paused"
                            and int(topic.get("lesson_content_ready_count") or 0) == 0]
                if not eligible:
                    # Keep the SQL candidate query and course-level progress projection fail-safe
                    # if their eligibility predicates ever diverge.
                    _release_worker_lease()
                    return {"status": "idle", "active": False,
                            "message": "کار ناتمامی برای ادامه پیدا نشد."}
                # Persist the complete eligible set before starting the first topic.
                # The remainder stays recoverable if the process exits during any capture.
                batch_key = str(int(time.time()))
                queued_run_ids: list[int] = []
                for topic_id in eligible:
                    request_key = f"playwright-lessons-{cid}-{topic_id}-resume-{batch_key}"
                    queued = service.create_run(
                        cid, run_type="lesson", topic_ids=[topic_id],
                        selection_mode="selected", request_key=request_key,
                    )
                    if queued["status"] != "queued":
                        raise ValueError("یکی از سرفصل‌های ادامهٔ یادگیری از قبل در صف یا در حال اجراست؛ وضعیت را بازخوانی کنید.")
                    queued_run_ids.append(int(queued["id"]))
                run = service.start_run(queued_run_ids[0], **_worker_lease_kwargs(epoch))
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
                _start_worker_thread(thread, run_id, cancel_event)
                _log_event(
                    "lesson_workflow_resumed",
                    run_id=run_id,
                    course_id=cid,
                    topic_ids=eligible,
                    queued_run_ids=queued_run_ids,
                )
                return {
                    "status": "resumed",
                    "active": True,
                    "run_id": run_id,
                    "course_id": cid,
                    "queued_topic_count": len(eligible),
                    "message": f"ادامهٔ {len(eligible)} سرفصل تأییدشده در صف پایدار ثبت شد و به‌ترتیب اجرا می‌شود.",
                }
            _release_worker_lease()
            return {"status": "idle", "active": False, "message": "کار ناتمامی برای ادامه پیدا نشد."}
    
        except Exception:
            _release_worker_lease()
            raise