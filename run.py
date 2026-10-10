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
import time
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


def _probe_local_wheelhouse(requirements: list[str]) -> subprocess.CompletedProcess[str]:
    """Resolve the full dependency graph using only artifacts already cached locally."""
    with tempfile.TemporaryDirectory(prefix="prerequisites-probe-") as temporary:
        return subprocess.run(
            [
                sys.executable, "-m", "pip", "download", "--no-index",
                "--find-links", str(WHEELHOUSE), "--dest", temporary, *requirements,
            ],
            cwd=ROOT, text=True, capture_output=True, check=False,
        )


def _ensure_wheelhouse(requirements: list[str], fingerprint: str) -> None:
    if _cache_is_complete(fingerprint):
        return

    # When no package artifacts exist yet, one resolver pass is the fastest way
    # to cache every direct and transitive dependency, even if already installed.
    if not _cached_package_pins():
        print("ساخت مخزن محلی پیش‌نیازها؛ دریافت بسته‌ها در prerequisites/wheels ...", flush=True)
        download = _run([
            sys.executable, "-m", "pip", "download",
            "--dest", str(WHEELHOUSE), *requirements,
        ])
        if download.returncode != 0:
            raise RuntimeError(
                "دریافت پیش‌نیازها ناموفق بود. اتصال اینترنت و دسترسی pip به مخزن بسته‌ها را بررسی کنید."
            )
        _write_cache_manifest(fingerprint)
        return

    # For a partial cache, first ask pip to resolve entirely offline. If an
    # artifact is missing, fetch only that missing distribution (without its
    # dependencies), then retry. Existing cached files are never fetched again.
    for _attempt in range(200):
        probe = _probe_local_wheelhouse(requirements)
        if probe.returncode == 0:
            _write_cache_manifest(fingerprint)
            return

        output = (probe.stdout or "") + "\n" + (probe.stderr or "")
        missing = re.search(r"No matching distribution found for ([^\r\n]+)", output)
        if not missing:
            raise RuntimeError(
                "بررسی آفلاین مخزن پیش‌نیازها شکست خورد و بسته مفقود قابل تشخیص نبود. "
                "جزئیات خطا: " + output[-1200:]
            )
        requirement = missing.group(1).strip().rstrip(".")
        print(f"بسته مفقود از مخزن محلی دریافت می‌شود: {requirement}", flush=True)
        download = _run([
            sys.executable, "-m", "pip", "download", "--no-deps",
            "--dest", str(WHEELHOUSE), "--find-links", str(WHEELHOUSE), requirement,
        ])
        if download.returncode != 0:
            raise RuntimeError(
                f"دریافت بسته مفقود «{requirement}» ناموفق بود؛ اجرای برنامه متوقف شد."
            )

    raise RuntimeError("تعداد تلاش‌های تکمیل مخزن پیش‌نیازها از حد مجاز گذشت.")


def _ensure_dependencies(dev: bool) -> None:
    PREREQUISITES.mkdir(parents=True, exist_ok=True)
    WHEELHOUSE.mkdir(parents=True, exist_ok=True)
    requirements = _requirements(dev) + ["setuptools>=68", "wheel"]
    fingerprint = _requirements_fingerprint(requirements, dev)

    # Keep an offline-reusable copy of every required package, regardless of
    # whether the current virtual environment already has it installed.
    _ensure_wheelhouse(requirements, fingerprint)

    print("بررسی و نصب وابستگی‌ها از مخزن محلی prerequisites/wheels ...", flush=True)
    install = _run([
        sys.executable, "-m", "pip", "install", "--no-index",
        "--find-links", str(WHEELHOUSE), *requirements,
    ])
    if install.returncode != 0:
        raise RuntimeError("نصب پیش‌نیازها از مخزن محلی ناموفق بود.")
    check = _run([sys.executable, "-m", "pip", "check"])
    if check.returncode != 0:
        raise RuntimeError("بررسی سازگاری وابستگی‌های نصب‌شده ناموفق بود.")
    project = _run([
        sys.executable, "-m", "pip", "install", "--no-index",
        "--find-links", str(WHEELHOUSE), "--no-build-isolation", "--no-deps", "-e", ".",
    ])
    if project.returncode != 0:
        raise RuntimeError("نصب خود پروژه ناموفق بود.")

def _download_once(url: str, destination: Path) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    manifest_path = destination.with_name(destination.name + ".download.json")
    part_path = destination.with_name(destination.name + ".part")
    part_manifest_path = destination.with_name(destination.name + ".part.json")

    if destination.is_file() and destination.stat().st_size > 0:
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            if int(manifest["size"]) == destination.stat().st_size and manifest.get("url") == url:
                print(f"استفاده از فایل ذخیره‌شده: {destination}", flush=True)
                return destination
        except (OSError, ValueError, TypeError, KeyError):
            pass
        print("فایل ذخیره‌شده تأیید نشده است؛ دانلود با قابلیت ادامه از سرور بررسی می‌شود.", flush=True)

    # Keep partial bytes in a deterministic file so the next run can send
    # an HTTP Range request instead of downloading from byte zero again.
    try:
        part_metadata = json.loads(part_manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        part_metadata = {}
    if part_path.exists() and part_metadata.get("url") != url:
        part_path.unlink(missing_ok=True)
    part_manifest_path.write_text(
        json.dumps({"url": url}, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    offset = part_path.stat().st_size if part_path.is_file() else 0
    if offset:
        print(
            f"ادامه دانلود از فایل موقت: {offset / (1024 * 1024):.1f} MB دریافت شده است.",
            flush=True,
        )
    else:
        print(f"شروع دانلود: {url}", flush=True)

    request_headers = {"Range": f"bytes={offset}-"} if offset else {}
    request = urllib.request.Request(url, headers=request_headers)
    started = time.monotonic()
    session_downloaded = 0
    try:
        response = urllib.request.urlopen(request, timeout=30)
        with response:
            status = getattr(response, "status", response.getcode())
            content_range = response.headers.get("Content-Range", "")
            range_match = re.match(r"bytes\s+(\d+)-(\d+)/(\d+|\*)", content_range)
            if offset and status == 206 and range_match and int(range_match.group(1)) == offset:
                mode = "ab"
                total = int(range_match.group(3)) if range_match.group(3) != "*" else 0
            else:
                # Server ignored Range (or returned an invalid range): restart
                # this response from byte zero rather than corrupting the file.
                mode = "wb"
                offset = 0
                total_header = response.headers.get("Content-Length")
                total = int(total_header) if total_header and total_header.isdigit() else 0

            with part_path.open(mode) as temporary:
                while True:
                    chunk = response.read(256 * 1024)
                    if not chunk:
                        break
                    temporary.write(chunk)
                    session_downloaded += len(chunk)
                    current_size = offset + session_downloaded
                    elapsed = max(time.monotonic() - started, 0.001)
                    speed_mb = session_downloaded / elapsed / (1024 * 1024)
                    done_mb = current_size / (1024 * 1024)
                    if total:
                        percent = min(100, current_size * 100 // total)
                        total_mb = total / (1024 * 1024)
                        bar_width = 24
                        filled = int(bar_width * percent / 100)
                        bar = "#" * filled + "-" * (bar_width - filled)
                        print(
                            f"\r[{bar}] {percent:3d}% | {done_mb:.1f}/{total_mb:.1f} MB | {speed_mb:.2f} MB/s",
                            end="", flush=True,
                        )
                    else:
                        print(
                            f"\rدریافت‌شده: {done_mb:.1f} MB | سرعت: {speed_mb:.2f} MB/s",
                            end="", flush=True,
                        )

        final_size = part_path.stat().st_size if part_path.exists() else 0
        if final_size == 0:
            raise RuntimeError(f"سرور فایل خالی برگرداند: {url}")
        if total and final_size != total:
            raise RuntimeError(
                f"دانلود هنوز کامل نیست: {final_size / (1024 * 1024):.1f} از "
                f"{total / (1024 * 1024):.1f} MB دریافت شده است. فایل موقت حفظ شد؛ "
                "دوباره run.py را اجرا کنید تا دانلود از ادامه ادامه یابد."
            )

        part_path.replace(destination)
        part_manifest_path.unlink(missing_ok=True)
        manifest_path.write_text(
            json.dumps({"size": final_size, "url": url}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        elapsed = max(time.monotonic() - started, 0.001)
        print(
            f"\nدانلود کامل شد: {destination} ({final_size / (1024 * 1024):.1f} MB؛ "
            f"{elapsed:.1f} ثانیه در این نوبت)",
            flush=True,
        )
        return destination
    except Exception:
        # Intentionally keep .part and its URL metadata for the next run.
        raise

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
    # Cache the official installer once for offline recovery, even if Ollama
    # is already installed. _download_once reuses the completed cached file.
    installer: Path | None = None
    if system == "Windows":
        installer = _download_once("https://ollama.com/download/OllamaSetup.exe", INSTALLERS / "OllamaSetup.exe")
    elif system == "Linux":
        installer = _download_once("https://ollama.com/install.sh", INSTALLERS / "ollama-install.sh")
    elif system == "Darwin":
        installer = _download_once("https://ollama.com/download/Ollama-darwin.zip", INSTALLERS / "Ollama-darwin.zip")

    # Installer download and installed-software detection are separate steps.
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

    listed = subprocess.run(
        [executable, "list"], cwd=ROOT, text=True, capture_output=True, check=False
    )
    if listed.stdout:
        print(listed.stdout, end="" if listed.stdout.endswith("\n") else "\n", flush=True)
    if listed.stderr:
        print(listed.stderr, file=sys.stderr, flush=True)
    if listed.returncode != 0:
        raise RuntimeError("Ollama نصب است اما سرویس آن در دسترس نیست. سرویس Ollama را اجرا کنید و دوباره تلاش کنید.")
    model = os.environ.get("OLLAMA_MODEL", DEFAULT_MODEL).strip() or DEFAULT_MODEL
    installed_models = {
        line.split()[0] for line in (listed.stdout or "").splitlines()
        if line.strip() and not line.lstrip().startswith("NAME ")
    }
    if model not in installed_models:
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
