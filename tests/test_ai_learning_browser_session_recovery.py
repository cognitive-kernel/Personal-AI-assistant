from __future__ import annotations

import threading

import pytest

from app.ai_learning import browser_automation


class FakeContext:
    def __init__(self):
        self.fail_pages = False
        self.closed = False
        self.handlers = {}

    @property
    def pages(self):
        if self.fail_pages:
            raise RuntimeError("browser context is disconnected")
        return [object()]

    def on(self, event, callback):
        self.handlers[event] = callback

    def close(self):
        self.closed = True
        callback = self.handlers.get("close")
        if callback is not None:
            callback()


class FakePlaywright:
    def __init__(self, context):
        self.chromium = self
        self.context = context
        self.stopped = False

    def launch_persistent_context(self, **kwargs):
        return self.context

    def stop(self):
        self.stopped = True


class FakePlaywrightManager:
    def __init__(self, playwright):
        self.playwright = playwright

    def start(self):
        return self.playwright


def _factory(monkeypatch, contexts):
    monkeypatch.setattr(browser_automation, "_BROWSER_SESSION", threading.local())
    monkeypatch.setattr(browser_automation, "_log_event", lambda *args, **kwargs: None)
    browser_automation._BROWSER_SESSION.reuse_enabled = True
    playwrighters = [FakePlaywright(context) for context in contexts]

    def create_manager():
        return FakePlaywrightManager(playwrighters.pop(0))

    return create_manager, playwrighters


def test_browser_session_relaunches_once_after_detected_context_loss(monkeypatch, tmp_path):
    first = FakeContext()
    second = FakeContext()
    factory, remaining = _factory(monkeypatch, [first, second])

    with browser_automation._browser_session(factory, "/fake/chrome", tmp_path / "profile") as (_, context):
        assert context is first

    first.fail_pages = True
    with browser_automation._browser_session(factory, "/fake/chrome", tmp_path / "profile") as (_, context):
        assert context is second

    assert first.closed is True
    assert second.closed is False
    assert remaining == []


def test_browser_session_stops_after_one_relaunch_if_context_dies_again(monkeypatch, tmp_path):
    first = FakeContext()
    second = FakeContext()
    third = FakeContext()
    factory, remaining = _factory(monkeypatch, [first, second, third])

    with browser_automation._browser_session(factory, "/fake/chrome", tmp_path / "profile") as (_, context):
        assert context is first

    first.fail_pages = True
    with browser_automation._browser_session(factory, "/fake/chrome", tmp_path / "profile") as (_, context):
        assert context is second

    second.fail_pages = True
    with pytest.raises(browser_automation.BrowserAutomationError) as error:
        with browser_automation._browser_session(factory, "/fake/chrome", tmp_path / "profile"):
            pytest.fail("a second context loss must require manual recovery")

    assert error.value.code == "chatgpt_browser_closed"
    assert len(remaining) == 1  # The third browser window was never launched.
    browser_automation._close_browser_session()
