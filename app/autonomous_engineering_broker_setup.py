"""Explicit, capability-scoped provisioning for the durable state broker.

Startup is read-only. Machine-level changes happen only after an explicit
loopback API action from the Autonomous Engineering UI or an operator.
"""
from __future__ import annotations

import errno
import os
import platform
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any

_SETUP_LOCK = threading.Lock()
_SETUP_TIMEOUT_SECONDS = 240
_HEALTH_TIMEOUT_MS = 30_000


@contextmanager
def _setup_process_lock():
    """Acquire a same-user cross-process OS lock for setup serialization."""
    if not _SETUP_LOCK.acquire(blocking=False):
        yield False
        return

    descriptor: int | None = None
    os_locked = False
    try:
        lock_path = Path(tempfile.gettempdir()) / "PersonalAIAssistant.StateBrokerSetup.lock"
        if os.name == "nt" and lock_path.is_symlink():
            raise BrokerSetupError(
                "فایل قفل نصب Broker معتبر نیست؛ مسیر قفل را بررسی کنید.",
                code="STATE_BROKER_SETUP_LOCK_INVALID",
                status_code=503,
            )
        flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(str(lock_path), flags, 0o600)
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise BrokerSetupError(
                "فایل قفل نصب Broker یک فایل معمولی نیست؛ عملیات برای ایمنی متوقف شد.",
                code="STATE_BROKER_SETUP_LOCK_INVALID",
                status_code=503,
            )
        if os.fstat(descriptor).st_size < 1:
            os.write(descriptor, b"\0")
            os.fsync(descriptor)
        os.lseek(descriptor, 0, os.SEEK_SET)

        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            os_locked = True
        except OSError as exc:
            if exc.errno not in {errno.EACCES, errno.EAGAIN, errno.EDEADLK, getattr(errno, "EDEADLOCK", errno.EDEADLK)}:
                raise
            yield False
            return

        yield True
    except BrokerSetupError:
        raise
    except OSError as exc:
        raise BrokerSetupError(
            "قفل بین‌پردازه‌ای نصب Broker قابل ایجاد یا اخذ نیست؛ عملیات تغییردهنده اجرا نشد.",
            code="STATE_BROKER_SETUP_LOCK_UNAVAILABLE",
            status_code=503,
            safe_details={"exception_type": type(exc).__name__},
        ) from exc
    finally:
        if descriptor is not None:
            if os_locked:
                try:
                    os.lseek(descriptor, 0, os.SEEK_SET)
                    if os.name == "nt":
                        import msvcrt

                        msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
                    else:
                        import fcntl

                        fcntl.flock(descriptor, fcntl.LOCK_UN)
                except OSError:
                    pass
            os.close(descriptor)
        _SETUP_LOCK.release()


class BrokerSetupError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        code: str,
        status_code: int = 503,
        retryable: bool = False,
        safe_details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code
        self.retryable = retryable
        self.safe_details = safe_details or {}


def _repository_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _broker_is_healthy() -> bool:
    from .autonomous_engineering_state_broker import health

    return health(timeout_ms=_HEALTH_TIMEOUT_MS, verify=True)


def _windows_setup_command(root: Path) -> tuple[list[str], str]:
    from .windows_powershell import find_compatible_powershell, windows_powershell_candidates

    candidates = windows_powershell_candidates(
        system=platform.system(),
        which=shutil.which,
        environ=os.environ,
    )
    selected = find_compatible_powershell(candidates, run=subprocess.run, cwd=root)
    if selected is None:
        raise BrokerSetupError(
            "هیچ میزبان PowerShell با قابلیت‌های لازم برای نصب Broker پیدا نشد؛ "
            "داشبورد و قابلیت‌های مستقل در حالت محدود باقی می‌مانند.",
            code="STATE_BROKER_POWERSHELL_UNAVAILABLE",
            status_code=503,
            safe_details={"candidate_count": len(candidates)},
        )
    executable, runtime = selected
    script = root / "scripts" / "ensure_autonomous_state_broker.ps1"
    if not script.is_file():
        raise BrokerSetupError(
            "اسکریپت نصب Broker در بستهٔ برنامه پیدا نشد.",
            code="STATE_BROKER_SETUP_SCRIPT_MISSING",
            status_code=500,
        )
    return [
        executable,
        "-NoLogo",
        "-NoProfile",
        "-NonInteractive",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(script),
    ], runtime


def _posix_setup_command(root: Path) -> tuple[list[str], str]:
    system = platform.system()
    script = root / "scripts" / "ensure_autonomous_state_broker_posix.py"
    if not script.is_file():
        raise BrokerSetupError(
            "راه‌انداز بومی Broker برای این سیستم‌عامل در بستهٔ برنامه موجود نیست.",
            code="STATE_BROKER_SETUP_SCRIPT_MISSING",
            status_code=500,
        )
    sudo = shutil.which("sudo")
    if not sudo:
        raise BrokerSetupError(
            "برای نصب سرویس Broker به sudo نیاز است؛ دستور راه‌انداز را از ترمینال مدیر سیستم اجرا کنید.",
            code="STATE_BROKER_ELEVATION_TOOL_UNAVAILABLE",
            status_code=403,
        )
    return [sudo, "-n", sys.executable, str(script)], f"{system} native adapter (sudo -n)"


def setup_state_broker() -> dict[str, Any]:
    """Install/repair the broker only after an explicit user action."""
    with _setup_process_lock() as acquired:
        if not acquired:
            raise BrokerSetupError(
                "عملیات نصب یا ترمیم Broker دیگری در حال اجراست؛ پس از پایان آن وضعیت را دوباره بررسی کنید.",
                code="STATE_BROKER_SETUP_IN_PROGRESS",
                status_code=409,
            )
        if _broker_is_healthy():
            return {
                "ready": True,
                "already_ready": True,
                "message": "سرویس فضای ثبت پیشرفت از قبل آماده است.",
            }
        return _setup_state_broker_locked()


def _setup_state_broker_locked() -> dict[str, Any]:
    """Run the provisioning operation while the cross-process lock is held."""
    root = _repository_root()
    system = platform.system()
    if system == "Windows":
        command, runtime = _windows_setup_command(root)
    elif system in {"Linux", "Darwin"}:
        command, runtime = _posix_setup_command(root)
    else:
        raise BrokerSetupError(
            f"نصب Broker برای سیستم‌عامل {system} پشتیبانی نشده است.",
            code="STATE_BROKER_PLATFORM_UNSUPPORTED",
            status_code=501,
        )

    try:
        result = subprocess.run(
            command,
            cwd=root,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=_SETUP_TIMEOUT_SECONDS,
            check=False,
            shell=False,
            stdin=subprocess.DEVNULL,
        )
    except subprocess.TimeoutExpired as exc:
        raise BrokerSetupError(
            "راه‌اندازی Broker از مهلت مجاز عبور کرد. وضعیت سرویس و گزارش نصب را بررسی کنید؛ "
            "درخواست نصب به‌صورت خودکار تکرار نمی‌شود.",
            code="STATE_BROKER_SETUP_TIMEOUT",
            status_code=504,
            safe_details={"timeout_seconds": _SETUP_TIMEOUT_SECONDS, "runtime": runtime},
        ) from exc
    except OSError as exc:
        raise BrokerSetupError(
            "اجرای میزبان نصب Broker ممکن نشد. مسیر و دسترسی ابزار انتخاب‌شده را بررسی کنید.",
            code="STATE_BROKER_SETUP_EXECUTION_FAILED",
            status_code=503,
            safe_details={"exception_type": type(exc).__name__, "runtime": runtime},
        ) from exc

    if result.returncode != 0:
        combined = ((result.stdout or "") + "\n" + (result.stderr or "")).casefold()
        if system == "Windows" and any(
            phrase in combined
            for phrase in ("canceled by the user", "cancelled by the user", "operation was canceled", "1223", "0x4c7")
        ):
            raise BrokerSetupError(
                "درخواست مجوز مدیر لغو شد؛ هیچ موفقیتی برای نصب Broker ثبت نشده است. "
                "در صورت تمایل، همین عملیات را دوباره از دکمهٔ راه‌اندازی اجرا کنید.",
                code="STATE_BROKER_ELEVATION_CANCELLED",
                status_code=403,
                safe_details={"runtime": runtime, "exit_code": result.returncode},
            )
        if system != "Windows" and any(
            phrase in combined
            for phrase in ("a password is required", "a terminal is required", "no tty present")
        ):
            raise BrokerSetupError(
                "ترمینال برای ورود رمز sudo در دسترس نیست؛ اسکریپت ensure_autonomous_state_broker_posix.py "
                "را از ترمینال مدیر سیستم اجرا کنید.",
                code="STATE_BROKER_ELEVATION_REQUIRED",
                status_code=403,
                safe_details={"runtime": runtime, "exit_code": result.returncode},
            )
        log_path = (
            str(Path(os.environ.get("ProgramData", r"C:\ProgramData")) / "PersonalAIAssistant" / "Logs" / "state-broker-setup.log")
            if system == "Windows"
            else None
        )
        raise BrokerSetupError(
            "نصب یا ترمیم Broker ناموفق بود؛ گزارش امن نصب را بررسی کنید. "
            "وضعیت وظیفه و checkpoint حذف یا بازنویسی نشده است.",
            code="STATE_BROKER_SETUP_FAILED",
            status_code=503,
            safe_details={
                "runtime": runtime,
                "exit_code": result.returncode,
                "log_path": log_path,
            },
        )

    if not _broker_is_healthy():
        raise BrokerSetupError(
            "اسکریپت نصب پایان یافت اما سرویس Broker پاسخ سلامت معتبر نداد؛ "
            "شروع چرخه تا تأیید وضعیت پایدار متوقف می‌ماند.",
            code="STATE_BROKER_POST_INSTALL_HEALTH_FAILED",
            status_code=503,
            safe_details={"runtime": runtime, "exit_code": result.returncode},
        )

    return {
        "ready": True,
        "already_ready": False,
        "runtime": runtime,
        "message": "سرویس فضای ثبت پیشرفت نصب/ترمیم شد و آزمون سلامت آن موفق بود.",
    }

