from __future__ import annotations

import pytest

from app.ai_learning import browser_automation
from app.ai_learning.browser_automation import BrowserAutomationError, _provider_gate_error


@pytest.mark.parametrize(
    ("url", "text", "expected_code"),
    [
        ("https://auth.openai.com/log-in", "", "guest_send_unavailable"),
        ("https://chatgpt.com/", "Please verify you are human to continue", "human_verification_required"),
        ("https://chatgpt.com/", "Too many requests. Try again later.", "provider_rate_limited"),
    ],
)
def test_chatgpt_provider_gates_are_classified_without_bypass(url, text, expected_code):
    error = _provider_gate_error(url, text)
    assert isinstance(error, BrowserAutomationError)
    assert error.code == expected_code


def test_provider_gate_classifier_ignores_normal_chat_content():
    assert _provider_gate_error(
        "https://chatgpt.com/c/example",
        "This lesson explains how HTTP 429 Too Many Requests works.",
    ) is None


class _FailingCloseContext:
    def close(self):
        raise RuntimeError("context close failed")


def test_browser_context_cleanup_does_not_mask_primary_failure(monkeypatch):
    events = []
    monkeypatch.setattr(
        "app.ai_learning.browser_automation._log_event",
        lambda event, **fields: events.append((event, fields)),
    )

    browser_automation._close_browser_context(
        _FailingCloseContext(), preserve_primary_error=True
    )

    assert events == [
        (
            "browser_context_close_failed",
            {
                "exception_type": "RuntimeError",
                "message": "context close failed",
                "primary_error_active": True,
            },
        )
    ]


def test_browser_context_cleanup_failure_is_visible_after_success(monkeypatch):
    monkeypatch.setattr(
        "app.ai_learning.browser_automation._log_event",
        lambda *args, **kwargs: None,
    )
    with pytest.raises(BrowserAutomationError) as raised:
        browser_automation._close_browser_context(
            _FailingCloseContext(), preserve_primary_error=False
        )
    assert raised.value.code == "browser_context_cleanup_failed"
