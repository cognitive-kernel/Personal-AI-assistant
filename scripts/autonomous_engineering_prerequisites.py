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
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

# Same reviewed version previously used by the workflow.
GH_VERSION = "2.104.0"
GH_ARCHIVE = f"gh_{GH_VERSION}_windows_amd64.zip"
GH_CHECKSUMS = f"gh_{GH_VERSION}_checksums.txt"
RELEASE_BASE = f"https://github.com/cli/cli/releases/download/v{GH_VERSION}"
GH_MEMBER = f"gh_{GH_VERSION}_windows_amd64/bin/gh.exe"
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


def ensure_gh(cache_root: Path, *, allow_install: bool, download=download_release, probe=probe_gh) -> Path:
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
    if not allow_install:
        raise RuntimeError("Compatible GitHub CLI missing; scoped installation was not authorized.")
    if platform.system() != "Windows" or platform.machine().casefold() not in {"amd64", "x86_64"}:
        raise RuntimeError("Automatic GitHub CLI installation is not approved for this platform.")
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
        if cache.exists():
            raise RuntimeError("Existing GitHub CLI cache needs explicit safe repair.")
        os.replace(stage_path, cache)
    return binary.absolute()


def main() -> None:
    # The workflow's explicit provisioning step is the installation authority;
    # ad-hoc invocations outside a Runner job do not install anything.
    runner_temp, github_path = os.getenv("RUNNER_TEMP"), os.getenv("GITHUB_PATH")
    if os.getenv("GITHUB_ACTIONS") != "true" or not runner_temp or not github_path:
        raise RuntimeError("Prerequisite installation requires the authorized Runner job.")
    root = Path(runner_temp)
    if not root.is_absolute():
        raise RuntimeError("Runner temporary directory must be absolute.")
    executable = ensure_gh(root / "paa-prerequisites", allow_install=True)
    with Path(github_path).open("a", encoding="utf-8") as stream:
        stream.write(str(executable.parent) + "\n")
    print("GitHub CLI version and required capabilities verified.")


if __name__ == "__main__":
    main()
