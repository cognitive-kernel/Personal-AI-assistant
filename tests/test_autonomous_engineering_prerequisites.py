from __future__ import annotations

import hashlib
import subprocess
import zipfile
from functools import partial

import pytest

from scripts import autonomous_engineering_prerequisites as prereq


def _fixture(root, content=b"approved binary"):
    archive = root / prereq.GH_ARCHIVE
    manifest = root / prereq.GH_CHECKSUMS
    with zipfile.ZipFile(archive, "w") as zipped:
        zipped.writestr(prereq.GH_MEMBER, content)
        zipped.writestr("../outside", b"must never extract")
    manifest.write_text(hashlib.sha256(archive.read_bytes()).hexdigest() + "  " + prereq.GH_ARCHIVE)
    return archive, manifest


def test_verified_extraction_only_extracts_approved_member(tmp_path):
    archive, manifest = _fixture(tmp_path)
    destination = tmp_path / "gh.exe"
    assert prereq.verified_extract(archive, manifest, destination) == hashlib.sha256(b"approved binary").hexdigest()
    assert destination.read_bytes() == b"approved binary"
    assert not (tmp_path.parent / "outside").exists()


def test_bad_checksum_prevents_extraction(tmp_path):
    archive, manifest = _fixture(tmp_path)
    manifest.write_text("0"*64 + "  " + prereq.GH_ARCHIVE)
    destination = tmp_path / "gh.exe"
    with pytest.raises(RuntimeError, match="SHA-256"):
        prereq.verified_extract(archive, manifest, destination)
    assert not destination.exists()


def test_no_authorization_does_not_download(monkeypatch, tmp_path):
    monkeypatch.setattr(prereq.shutil, "which", lambda _: None)
    with pytest.raises(RuntimeError, match="not authorized"):
        prereq.ensure_gh(tmp_path / "absent", allow_install=False,
                         download=lambda *a: pytest.fail("must not download"))
    assert not (tmp_path / "absent").exists()


def test_installs_from_pinned_bundle_and_reuses_cache_without_the_bundle(monkeypatch, tmp_path):
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    archive, manifest = _fixture(bundle)
    monkeypatch.setattr(prereq, "GH_ARCHIVE_SHA256", hashlib.sha256(archive.read_bytes()).hexdigest())
    monkeypatch.setattr(prereq.shutil, "which", lambda _: None)
    monkeypatch.setattr(prereq.platform, "system", lambda: "Windows")
    monkeypatch.setattr(prereq.platform, "machine", lambda: "AMD64")
    fetch = partial(prereq.copy_staged_gh, bundle)

    binary = prereq.ensure_gh(tmp_path / "cache", allow_install=True, download=fetch, probe=lambda _: True)
    assert binary.read_bytes() == b"approved binary"
    archive.unlink()
    manifest.unlink()
    assert prereq.ensure_gh(tmp_path / "cache", allow_install=True, download=fetch, probe=lambda _: True) == binary


def test_bundle_with_replaced_archive_and_manifest_is_rejected(tmp_path):
    archive, _ = _fixture(tmp_path)
    target = tmp_path / "copied.zip"
    with pytest.raises(RuntimeError, match="pinned SHA-256"):
        prereq.copy_staged_gh(tmp_path, archive.name, target, 1024 * 1024)
    assert not target.exists()


def test_probe_checks_capabilities_not_only_version(tmp_path):
    exe = tmp_path / "gh.exe"
    exe.write_bytes(b"test")
    def run(args, **kwargs):
        return subprocess.CompletedProcess(args, 0, "gh version " + prereq.GH_VERSION + "\n", "")
    assert not prereq.probe_gh(exe, run=run)


def test_download_origin_is_restricted():
    assert prereq._approved_url(prereq.RELEASE_BASE)
    assert not prereq._approved_url("https://github.com.evil.invalid/file")
    assert not prereq._approved_url("http://github.com/file")
