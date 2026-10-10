from __future__ import annotations

import importlib.metadata
import os
import platform
import re
import shutil
import subprocess
import sys
import venv
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PREREQUISITES = ROOT / "prerequisites"
WHEELHOUSE = PREREQUISITES / "wheels"
MIN_PYTHON = (3, 11)
DEFAULT_MODEL = "qwen2.5:7b"


def _run(command: list[str], *, cwd: Path = ROOT) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, cwd=cwd, text=True, check=False)


def _python_in_venv() -> Path:
    if os.name == "nt":
        return ROOT / ".venv" / "Scripts" / "python.exe"
    return ROOT / ".venv" / "bin" / "python"


def _inside_venv() -> bool:
    return sys.prefix != sys.base_prefix


def _requirements(dev: bool) -> list[str]:
    # Keep bootstrap usable without third-party imports; project metadata is TOML.
    import tomllib

    with (ROOT / "pyproject.toml").open("rb") as file:
        project = tomllib.load(file)["project"]
    requirements = list(project.get("dependencies", []))
    if dev:
        requirements.extend(project.get("optional-dependencies", {}).get("dev", []))
    return requirements


def _installed(requirement: str) -> bool:
    name = re.split(r"[<>=!~;\[]", requirement, maxsplit=1)[0].strip()
    normalized = re.sub(r"[-_.]+", "-", name).lower()
    try:
        distribution = importlib.metadata.distribution(normalized)
    except importlib.metadata.PackageNotFoundError:
        return False
    return bool(distribution.version)


def _ensure_dependencies(dev: bool) -> None:
    PREREQUISITES.mkdir(parents=True, exist_ok=True)
    WHEELHOUSE.mkdir(parents=True, exist_ok=True)
    requirements = _requirements(dev)
    missing = [requirement for requirement in requirements if not _installed(requirement)]
    tooling_missing = not _installed("setuptools>=68") or not _installed("wheel")
    check = _run([sys.executable, "-m", "pip", "check"])
    if missing or tooling_missing or check.returncode != 0:
        print("بررسی پیش‌نیازهای پایتون؛ دریافت بسته‌ها در پوشه prerequisites/wheels ...", flush=True)
        download = _run([
            sys.executable, "-m", "pip", "download", "--dest", str(WHEELHOUSE),
            "setuptools>=68", "wheel", *requirements,
        ])
        if download.returncode != 0:
            raise RuntimeError(
                "دریافت پیش‌نیازها ناموفق بود. اتصال اینترنت و دسترسی pip به مخزن بسته‌ها را بررسی کنید."
            )
        install = _run([
            sys.executable, "-m", "pip", "install", "--no-index",
            "--find-links", str(WHEELHOUSE), "--upgrade", "setuptools", "wheel", *requirements,
        ])
        if install.returncode != 0:
            raise RuntimeError("نصب پیش‌نیازهای پایتون از پوشه prerequisites ناموفق بود.")
    # Install the project itself in editable mode without another network request.
    project = _run([
        sys.executable, "-m", "pip", "install", "--no-index",
        "--find-links", str(WHEELHOUSE), "--no-build-isolation", "--no-deps", "-e", ".",
    ])
    if project.returncode != 0:
        raise RuntimeError("نصب خود پروژه ناموفق بود.")


def _ensure_ollama() -> None:
    executable = shutil.which("ollama")
    if not executable:
        system = platform.system()
        url = {
            "Windows": "https://ollama.com/download/OllamaSetup.exe",
            "Darwin": "https://ollama.com/download",
            "Linux": "https://ollama.com/download/linux",
        }.get(system, "https://ollama.com/download")
        raise RuntimeError(
            f"Ollama نصب نیست. نصب‌کننده رسمی را از {url} نصب کنید؛ سپس دوباره run.py را اجرا کنید."
        )

    model = os.environ.get("OLLAMA_MODEL", DEFAULT_MODEL).strip() or DEFAULT_MODEL
    listed = _run([executable, "list"])
    if listed.returncode != 0:
        raise RuntimeError(
            "Ollama نصب است اما سرویس آن در دسترس نیست. سرویس Ollama را اجرا کنید و دوباره تلاش کنید."
        )
    if model not in listed.stdout:
        print(f"مدل {model} نصب نیست؛ دریافت مدل شروع می‌شود ...", flush=True)
        pulled = _run([executable, "pull", model])
        if pulled.returncode != 0:
            raise RuntimeError(f"دریافت مدل {model} ناموفق بود؛ برنامه اجرا نشد.")


def main() -> int:
    if sys.version_info < MIN_PYTHON:
        print("این پروژه به Python 3.11 یا جدیدتر نیاز دارد. ابتدا Python را نصب/به‌روزرسانی کنید.")
        return 2

    # A standard-library-only launcher creates and re-enters the project venv.
    if not _inside_venv():
        python = _python_in_venv()
        if not python.exists():
            print("ساخت محیط مجازی پروژه ...", flush=True)
            try:
                venv.EnvBuilder(with_pip=True).create(ROOT / ".venv")
            except Exception as exc:
                print(f"ساخت محیط مجازی ناموفق بود: {exc}")
                return 2
        args = [str(python), str(Path(__file__).resolve()), "--managed-env", *sys.argv[1:]]
        return _run(args).returncode

    dev = "--dev" in sys.argv[1:]
    check_only = "--check-only" in sys.argv[1:]
    try:
        _ensure_dependencies(dev)
        _ensure_ollama()
    except RuntimeError as exc:
        print(f"پیش‌نیاز آماده نیست: {exc}", file=sys.stderr)
        return 2

    if check_only:
        print("بررسی پیش‌نیازها کامل شد.")
        return 0

    print("اجرای دستیار ...", flush=True)
    host = os.environ.get("HOST", "127.0.0.1")
    port = os.environ.get("PORT", "8000")
    return _run([
        sys.executable, "-m", "uvicorn", "app.main:app",
        "--host", host, "--port", port,
    ]).returncode


if __name__ == "__main__":
    raise SystemExit(main())
