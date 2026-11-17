from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

from app import browser_runtime
from app.ai_learning import browser_automation


def test_missing_playwright_package_is_installed_into_the_active_interpreter(monkeypatch):
    attempts = []
    commands = []
    factory = object()

    def import_sync_playwright():
        attempts.append("import")
        if len(attempts) < 3:
            raise ModuleNotFoundError("No module named 'playwright'")
        return factory

    monkeypatch.setattr(browser_runtime, "_import_sync_playwright", import_sync_playwright)
    monkeypatch.setattr(
        browser_runtime,
        "_run_bootstrap_command",
        lambda command, **kwargs: commands.append(command),
    )

    assert browser_runtime.load_sync_playwright() is factory
    assert commands == [[
        sys.executable,
        "-m",
        "pip",
        "install",
        "--disable-pip-version-check",
        browser_runtime.PLAYWRIGHT_PACKAGE,
    ]]


def test_missing_managed_chromium_is_downloaded_and_verified(tmp_path, monkeypatch):
    executable = tmp_path / "chromium" / "chrome.exe"
    commands = []

    class Manager:
        def __enter__(self):
            return SimpleNamespace(
                chromium=SimpleNamespace(executable_path=str(executable))
            )

        def __exit__(self, *_args):
            return False

    def install(command, **kwargs):
        commands.append(command)
        executable.parent.mkdir(parents=True)
        executable.write_bytes(b"browser fixture")

    monkeypatch.setattr(browser_runtime, "_run_bootstrap_command", install)

    actual = browser_runtime.ensure_managed_chromium(lambda: Manager())

    assert actual == executable
    assert executable.is_file()
    assert commands == [[sys.executable, "-m", "playwright", "install", "chromium"]]


def test_existing_managed_chromium_is_reused_without_download(tmp_path, monkeypatch):
    executable = tmp_path / "chromium" / "chrome.exe"
    executable.parent.mkdir()
    executable.write_bytes(b"existing browser")

    class Manager:
        def __enter__(self):
            return SimpleNamespace(
                chromium=SimpleNamespace(executable_path=str(executable))
            )

        def __exit__(self, *_args):
            return False

    def unexpected_install(*_args, **_kwargs):
        raise AssertionError("An existing browser must not be downloaded again.")

    monkeypatch.setattr(browser_runtime, "_run_bootstrap_command", unexpected_install)

    assert browser_runtime.ensure_managed_chromium(lambda: Manager()) == executable


def test_ai_learning_uses_managed_chromium_when_system_chrome_is_not_found(tmp_path, monkeypatch):
    executable = tmp_path / "managed" / "chrome.exe"
    monkeypatch.setattr(browser_automation, "_system_chrome_path", lambda: None)
    monkeypatch.setattr(
        browser_automation,
        "ensure_managed_chromium",
        lambda _factory: executable,
    )

    assert browser_automation._chrome_path(object()) == str(executable)


def test_startup_bootstrap_is_scheduled_in_a_background_thread(monkeypatch):
    import threading

    completed = threading.Event()
    calls = []

    def ensure_runtime():
        calls.append("started")
        completed.set()

    monkeypatch.setattr(browser_automation, "ensure_browser_runtime", ensure_runtime)
    monkeypatch.setattr(browser_automation, "_BROWSER_BOOTSTRAP_THREAD", None)

    browser_automation.schedule_browser_runtime_bootstrap()

    assert completed.wait(timeout=2)
    assert calls == ["started"]
    assert browser_automation._BROWSER_BOOTSTRAP_THREAD is not None
