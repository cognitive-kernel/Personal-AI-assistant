from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import venv
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PREREQUISITES = ROOT / "prerequisites"
WHEELHOUSE = PREREQUISITES / "wheels"
INSTALLERS = PREREQUISITES / "installers"
CACHE_MANIFEST = PREREQUISITES / "manifest.json"
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
    import tomllib

    with (ROOT / "pyproject.toml").open("rb") as file:
        project = tomllib.load(file)["project"]
    requirements = list(project.get("dependencies", []))
    if dev:
        requirements.extend(project.get("optional-dependencies", {}).get("dev", []))
    return requirements


def _requirement_name(requirement: str) -> str:
    name = re.split(r"[<>=!~;\[]", requirement, maxsplit=1)[0].strip()
    return re.sub(r"[-_.]+", "-", name).lower()


def _installed(requirement: str) -> bool:
    try:
        distribution = importlib.metadata.distribution(_requirement_name(requirement))
    except importlib.metadata.PackageNotFoundError:
        return False
    return bool(distribution.version)


def _cached_package_pins() -> dict[str, str]:
    """Return exact versions found in cached wheel/sdist filenames."""
    pins: dict[str, str] = {}
    if not WHEELHOUSE.exists():
        return pins
    for path in WHEELHOUSE.iterdir():
        name = path.name
        if path.suffix == ".whl":
            parts = name.split("-")
            if len(parts) >= 5:
                package, version = parts[0], parts[1]
            else:
                continue
        else:
            match = re.match(r"^(.+?)-([0-9][^-]*)\.(?:tar\.gz|zip)$", name)
            if not match:
                continue
            package, version = match.group(1), match.group(2)
        normalized = re.sub(r"[-_.]+", "-", package).lower()
        if normalized and version:
            pins[normalized] = version
    return pins


def _requirements_fingerprint(requirements: list[str], dev: bool) -> str:
    payload = {
        "requirements": sorted(requirements),
        "tooling": ["setuptools>=68", "wheel"],
        "dev": dev,
        "python": f"{sys.version_info.major}.{sys.version_info.minor}",
        "platform": sys.platform,
        "machine": platform.machine().lower(),
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()


def _cache_is_complete(fingerprint: str) -> bool:
    try:
        manifest = json.loads(CACHE_MANIFEST.read_text(encoding="utf-8"))
        if manifest.get("fingerprint") != fingerprint:
            return False
        files = manifest.get("files")
        return bool(files) and all((WHEELHOUSE / item).is_file() for item in files)
    except (OSError, ValueError, TypeError):
        return False


def _write_cache_manifest(fingerprint: str) -> None:
    files = sorted(
        path.name for path in WHEELHOUSE.iterdir()
        if path.is_file() and path.name != CACHE_MANIFEST.name
    )
    payload = {
        "fingerprint": fingerprint,
        "python": platform.python_version(),
        "platform": sys.platform,
        "machine": platform.machine(),
        "files": files,
    }
    CACHE_MANIFEST.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _ensure_dependencies(dev: bool) -> None:
    PREREQUISITES.mkdir(parents=True, exist_ok=True)
    WHEELHOUSE.mkdir(parents=True, exist_ok=True)
    requirements = _requirements(dev) + ["setuptools>=68", "wheel"]
    fingerprint = _requirements_fingerprint(requirements, dev)

    # Even if a dependency is already installed, cache its distribution for later
    # offline recovery. Existing cached distributions are offered to pip first.
    if not _cache_is_complete(fingerprint):
        pins = _cached_package_pins()
        constraints = PREREQUISITES / "cached-constraints.txt"
        constraints.write_text(
            "".join(f"{name}=={version}\n" for name, version in sorted(pins.items())),
            encoding="utf-8",
        )
        print("بررسی مخزن پیش‌نیازها؛ فقط بسته‌های موجودنبوده دریافت می‌شوند ...", flush=True)
        command = [
            sys.executable, "-m", "pip", "download",
            "--dest", str(WHEELHOUSE),
            "--find-links", str(WHEELHOUSE),
        ]
        if pins:
            command.extend(["--constraint", str(constraints)])
        command.extend(requirements)
        download = _run(command)
        if download.returncode != 0 and pins:
            # A cached version may no longer satisfy a changed version constraint.
            # Retry resolution without stale pins; pip still searches the local wheelhouse.
            download = _run([
                sys.executable, "-m", "pip", "download",
                "--dest", str(WHEELHOUSE), "--find-links", str(WHEELHOUSE), *requirements,
            ])
        if download.returncode != 0:
            raise RuntimeError(
                "آماده‌سازی مخزن پیش‌نیازها ناموفق بود. اتصال اینترنت و دسترسی pip را بررسی کنید."
            )
        _write_cache_manifest(fingerprint)

    missing = [item for item in requirements if not _installed(item)]
    check = _run([sys.executable, "-m", "pip", "check"])
    if missing or check.returncode != 0:
        print("نصب پیش‌نیازها از مخزن محلی prerequisites/wheels ...", flush=True)
        install = _run([
            sys.executable, "-m", "pip", "install", "--no-index",
            "--find-links", str(WHEELHOUSE), *requirements,
        ])
        if install.returncode != 0:
            raise RuntimeError("نصب پیش‌نیازها از مخزن محلی ناموفق بود.")
    project = _run([
        sys.executable, "-m", "pip", "install", "--no-index",
        "--find-links", str(WHEELHOUSE), "--no-build-isolation", "--no-deps", "-e", ".",
    ])
    if project.returncode != 0:
        raise RuntimeError("نصب خود پروژه ناموفق بود.")


def _download_once(url: str, destination: Path) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.is_file() and destination.stat().st_size > 0:
        return destination
    with urllib.request.urlopen(url, timeout=60) as response, tempfile.NamedTemporaryFile(
        dir=destination.parent, prefix=destination.name + ".", delete=False
    ) as temporary:
        temp_path = Path(temporary.name)
        shutil.copyfileobj(response, temporary)
    temp_path.replace(destination)
    return destination


def _find_ollama() -> str | None:
    found = shutil.which("ollama")
    if found:
        return found
    if os.name == "nt":
        candidates = [
            Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Ollama" / "ollama.exe",
            Path(os.environ.get("LOCALAPPDATA", "")) / "Ollama" / "ollama.exe",
            Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Ollama" / "ollama.exe",
        ]
    else:
        candidates = [Path.home() / ".local" / "bin" / "ollama", Path("/usr/local/bin/ollama"), Path("/usr/bin/ollama")]
    return str(next((item for item in candidates if item.is_file()), "")) or None


def _ensure_ollama() -> None:
    system = platform.system()
    installer: Path | None = None
    if system == "Windows":
        installer = _download_once("https://ollama.com/download/OllamaSetup.exe", INSTALLERS / "OllamaSetup.exe")
    elif system == "Linux":
        installer = _download_once("https://ollama.com/install.sh", INSTALLERS / "ollama-install.sh")
    elif system == "Darwin":
        # Keep the official archive for recovery even when Ollama is already installed.
        installer = _download_once("https://ollama.com/download/Ollama-darwin.zip", INSTALLERS / "Ollama-darwin.zip")

    executable = _find_ollama()
    if not executable and installer:
        print("Ollama نصب نیست؛ اجرای نصب‌کننده ذخیره‌شده در prerequisites/installers ...", flush=True)
        if system == "Windows":
            result = _run([str(installer)])
        elif system == "Linux":
            result = _run(["sh", str(installer)])
        else:
            # macOS app installation is intentionally left interactive; opening the
            # cached archive lets the user complete the signed app installation.
            _run(["open", str(installer)])
            raise RuntimeError(
                "بسته رسمی Ollama در prerequisites/installers ذخیره و باز شد. نصب برنامه را کامل کنید و دوباره run.py را اجرا کنید."
            )
        if result.returncode != 0:
            raise RuntimeError("نصب خودکار Ollama ناموفق بود؛ نصب‌کننده در prerequisites/installers باقی مانده است.")
        executable = _find_ollama()
    if not executable:
        raise RuntimeError("Ollama در PATH یا مسیرهای نصب متداول پیدا نشد؛ نصب‌کننده در prerequisites/installers قرار دارد.")

    listed = _run([executable, "list"])
    if listed.returncode != 0:
        raise RuntimeError("Ollama نصب است اما سرویس آن در دسترس نیست. سرویس Ollama را اجرا کنید و دوباره تلاش کنید.")
    model = os.environ.get("OLLAMA_MODEL", DEFAULT_MODEL).strip() or DEFAULT_MODEL
    if model not in listed.stdout:
        print(f"مدل {model} نصب نیست؛ دریافت مدل شروع می‌شود ...", flush=True)
        pulled = _run([executable, "pull", model])
        if pulled.returncode != 0:
            raise RuntimeError(f"دریافت مدل {model} ناموفق بود؛ برنامه اجرا نشد.")


def main() -> int:
    if sys.version_info < MIN_PYTHON:
        print("این پروژه به Python 3.11 یا جدیدتر نیاز دارد. ابتدا Python را نصب/به‌روزرسانی کنید.")
        return 2
    if not _inside_venv():
        python = _python_in_venv()
        if not python.exists():
            print("ساخت محیط مجازی پروژه ...", flush=True)
            try:
                venv.EnvBuilder(with_pip=True).create(ROOT / ".venv")
            except Exception as exc:
                print(f"ساخت محیط مجازی ناموفق بود: {exc}")
                return 2
        return _run([str(python), str(Path(__file__).resolve()), "--managed-env", *sys.argv[1:]]).returncode

    dev = "--dev" in sys.argv[1:]
    check_only = "--check-only" in sys.argv[1:]
    try:
        _ensure_dependencies(dev)
        _ensure_ollama()
    except (RuntimeError, OSError, urllib.error.URLError) as exc:
        print(f"پیش‌نیاز آماده نیست: {exc}", file=sys.stderr)
        return 2
    if check_only:
        print("بررسی و آماده‌سازی پیش‌نیازها کامل شد.")
        return 0
    print("اجرای دستیار ...", flush=True)
    return _run([
        sys.executable, "-m", "uvicorn", "app.main:app",
        "--host", os.environ.get("HOST", "127.0.0.1"),
        "--port", os.environ.get("PORT", "8000"),
    ]).returncode


if __name__ == "__main__":
    raise SystemExit(main())
