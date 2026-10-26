from __future__ import annotations

import pytest

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
