"""On-demand Playwright provisioning without user-specific browser paths.

The application owns the Python package and managed Chromium lifecycle. System
Chrome remains an optional override; no account name or fixed profile directory
is required.
"""
from __future__ import annotations

import importlib
import logging
import subprocess
import sys
import threading
from pathlib import Path
from typing import Callable

LOGGER = logging.getLogger("personal_ai_assistant.browser_runtime")
PLAYWRIGHT_PACKAGE = "playwright>=1.50,<2.0"
PACKAGE_INSTALL_TIMEOUT_SECONDS = 600
BROWSER_INSTALL_TIMEOUT_SECONDS = 900

_BOOTSTRAP_LOCK = threading.RLock()


class BrowserRuntimeSetupError(RuntimeError):
    """A browser dependency could not be installed or verified."""


def _import_sync_playwright() -> Callable:
    module = importlib.import_module("playwright.sync_api")
    factory = getattr(module, "sync_playwright", None)
    if not callable(factory):
        raise ImportError("playwright.sync_api does not expose sync_playwright")
    return factory


def _run_bootstrap_command(
    command: list[str],
    *,
    operation: str,
    timeout_seconds: int,
) -> None:
    LOGGER.info("%s started", operation)
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise BrowserRuntimeSetupError(
            f"{operation} timed out after {timeout_seconds} seconds."
        ) from exc
    except OSError as exc:
        raise BrowserRuntimeSetupError(
            f"{operation} could not start ({type(exc).__name__})."
        ) from exc

    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        if len(detail) > 2000:
            detail = detail[-2000:]
        suffix = f" Details: {detail}" if detail else ""
        raise BrowserRuntimeSetupError(
            f"{operation} failed with exit code {result.returncode}.{suffix}"
        )
    LOGGER.info("%s completed", operation)


def load_sync_playwright() -> Callable:
    """Import Playwright, installing its Python package into the active interpreter if absent."""
    try:
        return _import_sync_playwright()
    except ImportError:
        pass

    with _BOOTSTRAP_LOCK:
        # Another application thread may have completed installation while this
        # caller was waiting for the lock.
        try:
            return _import_sync_playwright()
        except ImportError:
            _run_bootstrap_command(
                [
                    sys.executable,
                    "-m",
                    "pip",
                    "install",
                    "--disable-pip-version-check",
                    PLAYWRIGHT_PACKAGE,
                ],
                operation="Automatic Playwright Python package installation",
                timeout_seconds=PACKAGE_INSTALL_TIMEOUT_SECONDS,
            )
            importlib.invalidate_caches()
            try:
                return _import_sync_playwright()
            except ImportError as exc:
                raise BrowserRuntimeSetupError(
                    "Playwright installation completed, but the active Python interpreter still cannot import playwright.sync_api."
                ) from exc


def _managed_chromium_executable(sync_playwright_factory: Callable) -> Path:
    try:
        with sync_playwright_factory() as playwright:
            executable = Path(playwright.chromium.executable_path)
    except Exception as exc:
        raise BrowserRuntimeSetupError(
            f"Could not inspect Playwright's managed Chromium installation ({type(exc).__name__})."
        ) from exc
    return executable


def ensure_managed_chromium(sync_playwright_factory: Callable | None = None) -> Path:
    """Return Playwright's Chromium executable, installing the matching browser if needed."""
    factory = sync_playwright_factory or load_sync_playwright()
    with _BOOTSTRAP_LOCK:
        executable = _managed_chromium_executable(factory)
        if executable.is_file():
            return executable

        _run_bootstrap_command(
            [sys.executable, "-m", "playwright", "install", "chromium"],
            operation="Automatic Playwright Chromium installation",
            timeout_seconds=BROWSER_INSTALL_TIMEOUT_SECONDS,
        )
        executable = _managed_chromium_executable(factory)
        if not executable.is_file():
            raise BrowserRuntimeSetupError(
                "Playwright reported a successful Chromium installation, but its executable is still missing."
            )
        return executable
