"""Capability-scoped GitHub CLI provisioning for the autonomous Runner.

Only the pinned upstream Windows x64 release is installed automatically.
Other platforms may reuse an already compatible executable. No elevation,
shell, global PATH mutation or arbitrary download location is used.
"""
from __future__ import annotations

import hashlib
import os
import platform
import re
import shutil
import stat
import subprocess
import tempfile
import time
import urllib.parse
import urllib.request
import zipfile
from functools import partial
from pathlib import Path

# Same reviewed version previously used by the workflow.
GH_VERSION = "2.104.0"
GH_ARCHIVE = f"gh_{GH_VERSION}_windows_amd64.zip"
GH_ARCHIVE_SHA256 = "08c06d10482f12c478018cd259f64acbd5f625712cadf331aad19ba973c879e5"
GH_CHECKSUMS = f"gh_{GH_VERSION}_checksums.txt"
RELEASE_BASE = f"https://github.com/cli/cli/releases/download/v{GH_VERSION}"
GH_MEMBER = "bin/gh.exe"
_MAX_ARCHIVE = 128 * 1024 * 1024
_MAX_BINARY = 128 * 1024 * 1024
_MAX_CHECKSUMS = 1024 * 1024
_RELEASE_HOSTS = {"github.com", "release-assets.githubusercontent.com",
                  "objects.githubusercontent.com", "github-releases.githubusercontent.com"}


def _approved_url(url: str) -> bool:
    parsed = urllib.parse.urlsplit(url)
    return (parsed.scheme == "https" and parsed.hostname in _RELEASE_HOSTS
            and parsed.port in (None, 443) and not parsed.username and not parsed.password)


class _ReleaseRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not _approved_url(newurl):
            raise RuntimeError("Unapproved GitHub release redirect.")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def download_release(name: str, target: Path, limit: int) -> None:
    if name not in {GH_ARCHIVE, GH_CHECKSUMS}:
        raise ValueError("Unapproved GitHub release asset.")
    opener = urllib.request.build_opener(_ReleaseRedirect())
    request = urllib.request.Request(
        f"{RELEASE_BASE}/{name}", headers={"User-Agent": "Personal-AI-Assistant-Runner"}
    )
    with opener.open(request, timeout=60) as response, target.open("xb") as stream:
        total = 0
        while True:
            chunk = response.read(1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > limit:
                raise RuntimeError("GitHub release asset exceeds the download limit.")
            stream.write(chunk)
        stream.flush()
        os.fsync(stream.fileno())


def _digest(path: Path, limit: int) -> str:
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
        raise RuntimeError("Invalid prerequisite artifact.")
    sha = hashlib.sha256()
    with path.open("rb") as stream:
        total = 0
        while chunk := stream.read(1024 * 1024):
            total += len(chunk)
            if total > limit:
                raise RuntimeError("Prerequisite artifact exceeds its limit.")
            sha.update(chunk)
    return sha.hexdigest()


def expected_digest(checksum_file: Path) -> str:
    if checksum_file.stat().st_size > _MAX_CHECKSUMS:
        raise RuntimeError("Checksum manifest exceeds its limit.")
    matches = []
    for line in checksum_file.read_text(encoding="utf-8").splitlines():
        match = re.fullmatch(r"([0-9A-Fa-f]{64})[ \t]+\*?([^\s]+)", line.strip())
        if match and match.group(2) == GH_ARCHIVE:
            matches.append(match.group(1).lower())
    if len(matches) != 1:
        raise RuntimeError("Official checksum manifest does not uniquely identify the approved archive.")
    return matches[0]


def verified_extract(archive: Path, checksums: Path, destination: Path) -> str:
    if _digest(archive, _MAX_ARCHIVE) != expected_digest(checksums):
        raise RuntimeError("GitHub CLI archive failed SHA-256 verification.")
    # Extract only the exact reviewed member, never extractall or an archive path.
    with zipfile.ZipFile(archive) as zipped:
        matches = [i for i in zipped.infolist() if i.filename == GH_MEMBER]
        if len(matches) != 1 or matches[0].is_dir() or matches[0].file_size > _MAX_BINARY:
            raise RuntimeError("Approved GitHub CLI executable is missing or invalid.")
        info = matches[0]
        unix_mode = info.external_attr >> 16
        if stat.S_ISLNK(unix_mode):
            raise RuntimeError("Executable in GitHub CLI archive is a symbolic link.")
        with zipped.open(info) as source, destination.open("xb") as target:
            total = 0
            while chunk := source.read(1024 * 1024):
                total += len(chunk)
                if total > _MAX_BINARY:
                    raise RuntimeError("GitHub CLI executable exceeds its limit.")
                target.write(chunk)
            target.flush()
            os.fsync(target.fileno())
    return _digest(destination, _MAX_BINARY)


def stage_gh_bundle(directory: Path) -> None:
    """Download the pinned release on a GitHub-hosted worker."""
    directory.mkdir(parents=True, exist_ok=True)
    download_release(GH_CHECKSUMS, directory / GH_CHECKSUMS, _MAX_CHECKSUMS)
    download_release(GH_ARCHIVE, directory / GH_ARCHIVE, _MAX_ARCHIVE)
    if expected_digest(directory / GH_CHECKSUMS) != GH_ARCHIVE_SHA256:
        raise RuntimeError("GitHub CLI release did not match the pinned SHA-256.")
    with tempfile.TemporaryDirectory(prefix="gh-bundle-check-") as scratch:
        verified_extract(directory / GH_ARCHIVE, directory / GH_CHECKSUMS, Path(scratch) / "gh.exe")


def copy_staged_gh(directory: Path, name: str, target: Path, limit: int) -> None:
    if name not in {GH_ARCHIVE, GH_CHECKSUMS}:
        raise ValueError("Unapproved GitHub CLI artifact member.")
    source = directory / name
    if not source.is_file():
        raise RuntimeError("The pinned GitHub CLI bundle is missing. Check the Windows runtime package job and artifact download.")
    if name == GH_ARCHIVE and _digest(source, limit) != GH_ARCHIVE_SHA256:
        raise RuntimeError("GitHub CLI artifact did not match the pinned SHA-256.")
    if name == GH_CHECKSUMS and expected_digest(source) != GH_ARCHIVE_SHA256:
        raise RuntimeError("GitHub CLI checksum manifest did not match the pinned SHA-256.")
    shutil.copyfile(source, target)


def probe_gh(executable: Path, *, run=subprocess.run) -> bool:
    try:
        if not executable.is_absolute() or not executable.is_file():
            return False
        if os.name == "nt" and executable.suffix.casefold() != ".exe":
            return False
        version = run([str(executable), "--version"], capture_output=True, text=True, timeout=8, check=False)
        if version.returncode or not re.match(r"^gh version " + re.escape(GH_VERSION) + r"(?:\s|$)", version.stdout):
            return False
        for args, required in [
            (["api", "--help"], "--method"),
            (["workflow", "run", "--help"], "--ref"),
        ]:
            result = run([str(executable), *args], capture_output=True, text=True, timeout=8, check=False)
            if result.returncode or required not in result.stdout:
                return False
        return True
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return False


def find_gh(cache_root: Path, *, probe=probe_gh) -> Path | None:
    found = shutil.which("gh")
    if found and probe(Path(found).absolute()):
        return Path(found).absolute()
    # Existing cached release is validated using its official manifest and the
    # hash of the extracted approved executable before it can ever be executed.
    cache = cache_root / f"gh-{GH_VERSION}-windows-amd64"
    binary = cache / "gh.exe"
    archive, checksums = cache / GH_ARCHIVE, cache / GH_CHECKSUMS
    if cache.is_dir() and not cache.is_symlink():
        try:
            with tempfile.TemporaryDirectory(dir=cache_root, prefix=".gh-verify-") as stage:
                extracted = Path(stage) / "gh.exe"
                digest = verified_extract(archive, checksums, extracted)
                if _digest(binary, _MAX_BINARY) == digest and probe(binary.absolute()):
                    return binary.absolute()
        except (OSError, RuntimeError, ValueError, zipfile.BadZipFile):
            pass
    return None


def ensure_gh(cache_root: Path, *, allow_install: bool, download=download_release, probe=probe_gh) -> Path:
    if existing := find_gh(cache_root, probe=probe):
        return existing
    if not allow_install:
        raise RuntimeError("Compatible GitHub CLI missing; scoped installation was not authorized.")
    if platform.system() != "Windows" or platform.machine().casefold() not in {"amd64", "x86_64"}:
        raise RuntimeError("Automatic GitHub CLI installation is not approved for this platform.")
    cache = cache_root / f"gh-{GH_VERSION}-windows-amd64"
    cache_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    if cache_root.is_symlink() or cache.is_symlink():
        raise RuntimeError("Prerequisite cache cannot be a symbolic link.")
    # Fresh verified stage; an invalid existing cache is not deleted or reused.
    with tempfile.TemporaryDirectory(dir=cache_root, prefix=".gh-install-") as stage:
        stage_path = Path(stage)
        download(GH_CHECKSUMS, stage_path / GH_CHECKSUMS, _MAX_CHECKSUMS)
        download(GH_ARCHIVE, stage_path / GH_ARCHIVE, _MAX_ARCHIVE)
        candidate = stage_path / "gh.exe"
        verified_extract(stage_path / GH_ARCHIVE, stage_path / GH_CHECKSUMS, candidate)
        if not probe(candidate.absolute()):
            raise RuntimeError("Downloaded GitHub CLI version or capability verification failed.")
        # Publish a complete verified directory atomically; never overwrite
        # another run's cache. A racing caller can revalidate the winning copy.
        for delay in (0.1, 0.25, 0.5, 1.0, None):
            if cache.exists() or cache.is_symlink():
                raise RuntimeError("Existing GitHub CLI cache needs explicit safe repair.")
            try:
                os.replace(stage_path, cache)
                break
            except OSError as exc:
                # Windows scanners can hold the verified executable briefly after probing it.
                if delay is None or getattr(exc, "winerror", None) not in {5, 32, 33}:
                    raise
                time.sleep(delay)
    return (cache / "gh.exe").absolute()


def runner_path(name: str) -> Path:
    path = Path(os.getenv(name, ""))
    if os.getenv("GITHUB_ACTIONS") != "true" or not path.is_absolute():
        raise RuntimeError(f"Prerequisite setup requires an absolute {name} in the authorized Runner job.")
    return path


def runner_gh_cache() -> Path:
    return runner_path("RUNNER_TOOL_CACHE") / "PersonalAIAssistant" / "prerequisites"


def report_gh_cache() -> None:
    output = runner_path("GITHUB_OUTPUT")
    cached = find_gh(runner_gh_cache()) is not None
    with output.open("a", encoding="utf-8") as stream:
        stream.write(f"cached={str(cached).lower()}\n")


def main() -> None:
    github_path = runner_path("GITHUB_PATH")
    bundle = runner_path("RUNNER_TEMP") / "engineering-runner-gh"
    executable = ensure_gh(runner_gh_cache(), allow_install=True, download=partial(copy_staged_gh, bundle))
    with github_path.open("a", encoding="utf-8") as stream:
        stream.write(str(executable.parent) + "\n")
    print("GitHub CLI version and required capabilities verified.")


if __name__ == "__main__":
    main()
