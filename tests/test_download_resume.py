import hashlib
import importlib.util
import json
import subprocess
import urllib.request
from pathlib import Path

import pytest

_RUN_PATH = Path(__file__).resolve().parents[1] / "run.py"
_RUN_SPEC = importlib.util.spec_from_file_location("installer_run", _RUN_PATH)
assert _RUN_SPEC is not None and _RUN_SPEC.loader is not None
installer = importlib.util.module_from_spec(_RUN_SPEC)
_RUN_SPEC.loader.exec_module(installer)


class PartialResponse:
    status = 206

    def __init__(self, content_range):
        self.headers = {"Content-Range": content_range, "Content-Length": "5"}

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def getcode(self):
        return self.status

    def read(self, _size=-1):
        raise AssertionError("invalid partial responses must be rejected before reading")


class DownloadResponse:
    def __init__(self, status, body, headers=None):
        self.status = status
        self.body = body
        self.headers = headers or {}
        self._read = False

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def getcode(self):
        return self.status

    def read(self, _size=-1):
        if self._read:
            return b""
        self._read = True
        return self.body


@pytest.mark.parametrize(
    ("existing_bytes", "content_range"),
    [
        (b"", "bytes 0-4/10"),
        (b"abc", "bytes 0-4/10"),
        (b"abc", "not-a-content-range"),
        (b"abc", "bytes 3-5/6trailing-junk"),
    ],
)
def test_invalid_partial_response_is_rejected_without_replacing_partial_file(
    tmp_path, monkeypatch, existing_bytes, content_range
):
    destination = tmp_path / "installer.bin"
    part_path = destination.with_name(destination.name + ".part")
    part_manifest_path = destination.with_name(destination.name + ".part.json")
    url = "https://downloads.example.invalid/installer.bin"

    if existing_bytes:
        part_path.write_bytes(existing_bytes)
    part_manifest_path.write_text(json.dumps({"url": url}), encoding="utf-8")
    monkeypatch.setattr(
        installer.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: PartialResponse(content_range),
    )

    with pytest.raises(RuntimeError, match="بازهٔ نامعتبر"):
        installer._download_once(url, destination)

    assert part_path.exists() is bool(existing_bytes)
    if existing_bytes:
        assert part_path.read_bytes() == existing_bytes
    assert not destination.exists()


def test_valid_partial_response_appends_only_the_requested_range(tmp_path, monkeypatch):
    destination = tmp_path / "installer.bin"
    part_path = destination.with_name(destination.name + ".part")
    part_manifest_path = destination.with_name(destination.name + ".part.json")
    url = "https://downloads.example.invalid/installer.bin"
    part_path.write_bytes(b"abc")
    part_manifest_path.write_text(json.dumps({"url": url}), encoding="utf-8")
    response = DownloadResponse(
        206, b"def", {"Content-Range": "bytes 3-5/6", "Content-Length": "3"}
    )
    captured = {}

    def fake_urlopen(request, **_kwargs):
        captured["range"] = request.get_header("Range")
        return response

    monkeypatch.setattr(installer.urllib.request, "urlopen", fake_urlopen)

    result = installer._download_once(url, destination)

    assert captured["range"] == "bytes=3-"
    assert result == destination
    assert destination.read_bytes() == b"abcdef"
    assert not part_path.exists()
    assert not part_manifest_path.exists()
    assert json.loads(destination.with_name(destination.name + ".download.json").read_text()) == {
        "size": 6,
        "url": url,
    }


def test_http_200_range_fallback_restarts_from_zero(tmp_path, monkeypatch):
    destination = tmp_path / "installer.bin"
    part_path = destination.with_name(destination.name + ".part")
    part_manifest_path = destination.with_name(destination.name + ".part.json")
    url = "https://downloads.example.invalid/installer.bin"
    part_path.write_bytes(b"stale")
    part_manifest_path.write_text(json.dumps({"url": url}), encoding="utf-8")
    response = DownloadResponse(200, b"fresh", {"Content-Length": "5"})
    captured = {}

    def fake_urlopen(request, **_kwargs):
        captured["range"] = request.get_header("Range")
        return response

    monkeypatch.setattr(installer.urllib.request, "urlopen", fake_urlopen)

    result = installer._download_once(url, destination)

    assert captured["range"] == "bytes=5-"
    assert result == destination
    assert destination.read_bytes() == b"fresh"
    assert not part_path.exists()
    assert not part_manifest_path.exists()
    assert json.loads(destination.with_name(destination.name + ".download.json").read_text()) == {
        "size": 5,
        "url": url,
    }

def test_truncated_partial_response_with_unknown_total_is_not_promoted(tmp_path, monkeypatch):
    destination = tmp_path / "installer.bin"
    part_path = destination.with_name(destination.name + ".part")
    part_manifest_path = destination.with_name(destination.name + ".part.json")
    url = "https://downloads.example.invalid/installer.bin"
    part_path.write_bytes(b"abc")
    part_manifest_path.write_text(json.dumps({"url": url}), encoding="utf-8")
    response = DownloadResponse(
        206, b"de", {"Content-Range": "bytes 3-5/*", "Content-Length": "2"}
    )
    monkeypatch.setattr(
        installer.urllib.request, "urlopen", lambda *_args, **_kwargs: response
    )

    with pytest.raises(RuntimeError, match="دانلود ناقص پاسخ بازه‌ای"):
        installer._download_once(url, destination)

    assert part_path.read_bytes() == b"abcde"
    assert not destination.exists()
    assert part_manifest_path.exists()



def test_download_verifies_expected_sha256_and_records_digest(tmp_path, monkeypatch):
    destination = tmp_path / "verified-installer.bin"
    url = "https://github.com/ollama/ollama/releases/download/v0.40.13/install.sh"
    body = b"verified release asset"
    expected_sha256 = hashlib.sha256(body).hexdigest()
    response = DownloadResponse(200, body, {"Content-Length": str(len(body))})
    monkeypatch.setattr(
        installer.urllib.request, "urlopen", lambda *_args, **_kwargs: response
    )

    result = installer._download_once(
        url,
        destination,
        expected_sha256=expected_sha256,
        expected_size=len(body),
    )

    assert result.read_bytes() == body
    manifest = json.loads(
        destination.with_name(destination.name + ".download.json").read_text()
    )
    assert manifest == {
        "size": len(body),
        "url": url,
        "sha256": expected_sha256,
        "expected_size": len(body),
    }


def test_download_hash_mismatch_never_replaces_existing_installer(tmp_path, monkeypatch):
    destination = tmp_path / "verified-installer.bin"
    destination.write_bytes(b"previous-valid")
    url = "https://github.com/ollama/ollama/releases/download/v0.40.13/install.sh"
    old_manifest = {
        "size": len(b"previous-valid"),
        "url": url,
        "sha256": hashlib.sha256(b"previous-valid").hexdigest(),
    }
    destination.with_name(destination.name + ".download.json").write_text(
        json.dumps(old_manifest), encoding="utf-8"
    )
    response = DownloadResponse(200, b"tampered", {"Content-Length": "8"})
    monkeypatch.setattr(
        installer.urllib.request, "urlopen", lambda *_args, **_kwargs: response
    )

    with pytest.raises(RuntimeError, match="SHA-256"):
        installer._download_once(
            url,
            destination,
            expected_sha256=hashlib.sha256(b"expected").hexdigest(),
            expected_size=8,
        )

    assert destination.read_bytes() == b"previous-valid"
    assert not destination.with_name(destination.name + ".part").exists()
    assert not destination.with_name(destination.name + ".part.json").exists()


def test_verified_cached_installer_is_reused_only_when_bytes_match(tmp_path, monkeypatch):
    destination = tmp_path / "verified-installer.bin"
    url = "https://github.com/ollama/ollama/releases/download/v0.40.13/install.sh"
    body = b"cached verified installer"
    expected_sha256 = hashlib.sha256(body).hexdigest()
    destination.write_bytes(body)
    destination.with_name(destination.name + ".download.json").write_text(
        json.dumps({"size": len(body), "url": url}), encoding="utf-8"
    )
    monkeypatch.setattr(
        installer.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: pytest.fail("verified cache should not download again"),
    )

    result = installer._download_once(
        url, destination, expected_sha256=expected_sha256, expected_size=len(body)
    )

    assert result == destination
    manifest = json.loads(
        destination.with_name(destination.name + ".download.json").read_text()
    )
    assert manifest["sha256"] == expected_sha256
    assert manifest["expected_size"] == len(body)


def test_complete_verified_partial_is_promoted_without_network(tmp_path, monkeypatch):
    destination = tmp_path / "verified-installer.bin"
    part_path = destination.with_name(destination.name + ".part")
    part_manifest_path = destination.with_name(destination.name + ".part.json")
    url = "https://github.com/ollama/ollama/releases/download/v0.40.13/install.sh"
    body = b"complete but not yet promoted"
    expected_sha256 = hashlib.sha256(body).hexdigest()
    part_path.write_bytes(body)
    part_manifest_path.write_text(
        json.dumps({
            "url": url,
            "sha256": expected_sha256,
            "expected_size": len(body),
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        installer.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: pytest.fail("complete verified partial should not be downloaded"),
    )

    result = installer._download_once(
        url,
        destination,
        expected_sha256=expected_sha256,
        expected_size=len(body),
    )

    assert result.read_bytes() == body
    assert not part_path.exists()
    assert not part_manifest_path.exists()


def test_download_rejects_content_larger_than_official_asset_size(tmp_path, monkeypatch):
    destination = tmp_path / "verified-installer.bin"
    url = "https://github.com/ollama/ollama/releases/download/v0.40.13/install.sh"
    response = DownloadResponse(200, b"too-large", {"Content-Length": "9"})
    monkeypatch.setattr(
        installer.urllib.request, "urlopen", lambda *_args, **_kwargs: response
    )

    with pytest.raises(RuntimeError, match="اندازهٔ رسمی دارایی"):
        installer._download_once(
            url,
            destination,
            expected_sha256=hashlib.sha256(b"small").hexdigest(),
            expected_size=5,
        )
    assert not destination.exists()


def test_latest_ollama_release_requires_official_asset_digest_and_origin(monkeypatch):
    tag = "v0.40.13"
    names = ("install.sh", "OllamaSetup.exe", "Ollama-darwin.zip", "ollama-linux-amd64.tar.zst", "ollama-linux-amd64-rocm.tar.zst")
    assets = [
        {
            "name": name,
            "browser_download_url": f"https://github.com/ollama/ollama/releases/download/{tag}/{name}",
            "digest": "sha256:" + (str(index + 1) * 64),
            "size": 100 + index,
        }
        for index, name in enumerate(names)
    ]
    captured = {}

    class Response:
        def __enter__(self): return self
        def __exit__(self, *_args): return False
        def read(self, limit):
            captured["read_limit"] = limit
            return json.dumps({
                "tag_name": tag,
                "draft": False,
                "prerelease": False,
                "assets": assets,
            }).encode()

    class Opener:
        def open(self, request, timeout):
            captured["request"] = request
            captured["timeout"] = timeout
            return Response()

    def build_opener(*handlers):
        captured["handlers"] = handlers
        return Opener()

    monkeypatch.setattr(
        installer.urllib.request, "build_opener", build_opener
    )
    result = installer._latest_ollama_release_assets()

    assert result["tag"] == tag
    assert result["assets"]["OllamaSetup.exe"]["sha256"] == "2" * 64
    assert result["assets"]["OllamaSetup.exe"]["size"] == 101
    assert result["assets"]["ollama-linux-amd64.tar.zst"]["sha256"] == "4" * 64
    assert result["assets"]["ollama-linux-amd64.tar.zst"]["size"] == 103
    assert captured["request"].full_url == installer._OLLAMA_RELEASE_API
    assert captured["timeout"] == 15
    assert captured["read_limit"] == 2 * 1024 * 1024 + 1
    assert any(
        isinstance(handler, urllib.request.ProxyHandler) and handler.proxies == {}
        for handler in captured["handlers"]
    )
    assert any(
        isinstance(handler, installer._NoRedirectHandler)
        for handler in captured["handlers"]
    )


def test_latest_ollama_release_rejects_untrusted_asset_url(monkeypatch):
    tag = "v0.40.13"
    payload = {
        "tag_name": tag,
        "draft": False,
        "prerelease": False,
        "assets": [{
            "name": "install.sh",
            "browser_download_url": "https://evil.example/install.sh",
            "digest": "sha256:" + "a" * 64,
            "size": 10,
        }],
    }

    class Response:
        def __enter__(self): return self
        def __exit__(self, *_args): return False
        def read(self, _limit): return json.dumps(payload).encode()

    class Opener:
        def open(self, *_args, **_kwargs): return Response()

    monkeypatch.setattr(
        installer.urllib.request,
        "build_opener",
        lambda *_args, **_kwargs: Opener(),
    )
    with pytest.raises(RuntimeError, match="مبدأ دانلود"):
        installer._latest_ollama_release_assets()


def test_github_asset_redirect_handler_rejects_non_github_hosts():
    handler = installer._GitHubAssetRedirectHandler()
    request = urllib.request.Request(
        "https://github.com/ollama/ollama/releases/download/v0.40.13/install.sh"
    )
    assert handler.redirect_request(
        request, None, 302, "Found", {}, "https://evil.example/installer.exe"
    ) is None


def test_github_asset_redirect_handler_allows_github_release_asset_host():
    handler = installer._GitHubAssetRedirectHandler()
    request = urllib.request.Request(
        "https://github.com/ollama/ollama/releases/download/v0.40.13/install.sh"
    )
    redirected = handler.redirect_request(
        request, None, 302, "Found", {},
        "https://release-assets.githubusercontent.com/asset?token=redacted",
    )
    assert redirected is not None
    assert redirected.full_url.startswith("https://release-assets.githubusercontent.com/")


def test_linux_installer_is_pinned_to_the_verified_release_tag(tmp_path, monkeypatch):
    installer_path = tmp_path / "ollama-install.sh"
    release = {
        "tag": "v0.40.13",
        "assets": {
            "install.sh": {
                "url": "https://github.com/ollama/ollama/releases/download/v0.40.13/install.sh",
                "sha256": "a" * 64,
                "size": 15902,
            },
            "ollama-linux-amd64.tar.zst": {
                "url": "https://github.com/ollama/ollama/releases/download/v0.40.13/ollama-linux-amd64.tar.zst",
                "sha256": "b" * 64,
                "size": 123456,
            },
        },
    }
    calls = []
    find_results = iter([None, "/usr/bin/ollama"])

    verified_installer_path = tmp_path / "ollama-install-verified.sh"
    monkeypatch.setattr(installer.platform, "system", lambda: "Linux")
    monkeypatch.setattr(installer.platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(installer, "_patch_linux_install_script", lambda _path: verified_installer_path)
    monkeypatch.setattr(installer, "_find_ollama", lambda: next(find_results))
    monkeypatch.setattr(installer, "_latest_ollama_release_assets", lambda: release)
    monkeypatch.setattr(
        installer,
        "_download_once",
        lambda url, destination, **kwargs: (
            calls.append((url, destination, kwargs)) or installer_path
        ),
    )

    def fake_run(command, *, cwd=installer.ROOT, env=None):
        calls.append((command, env))
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(installer, "_run", fake_run)
    monkeypatch.setattr(
        installer.subprocess,
        "run",
        lambda command, **kwargs: subprocess.CompletedProcess(
            command, 0, "NAME ID SIZE\nqwen2.5:7b abc 1GB\n", ""
        ),
    )

    installer._ensure_ollama()

    install_call = next(item for item in calls if isinstance(item[0], list))
    assert install_call[0] == ["sh", str(verified_installer_path)]
    assert install_call[1]["OLLAMA_VERSION"] == "0.40.13"
    assert install_call[1]["OLLAMA_VERIFIED_ASSET_DIR"].endswith("ollama-release-assets")
    assert install_call[1]["OLLAMA_VERIFIED_PYTHON"] == installer.sys.executable
    pinned = json.loads(install_call[1]["OLLAMA_VERIFIED_RELEASE_ASSETS_JSON"])
    assert pinned["tag"] == "v0.40.13"
    assert "ollama-linux-amd64.tar.zst" in pinned["assets"]
    download_call = next(item for item in calls if isinstance(item[0], str))
    assert download_call[2]["expected_sha256"] == "a" * 64
    assert download_call[2]["expected_size"] == 15902

def test_linux_installer_patch_replaces_second_stage_curl_with_verified_helper(tmp_path):
    original = tmp_path / "ollama-install.sh"
    original.write_text(
        "download_and_extract() {\n"
        "    curl --fail --show-error https://ollama.com/download/ollama-linux-amd64.tar.zst | zstd -d | tar -xf -\n"
        "}\n\n"
        "for BINDIR in /usr/local/bin /usr/bin /bin; do\n"
        "    echo ready\n"
        "done\n",
        encoding="utf-8",
    )

    patched = installer._patch_linux_install_script(original)
    text = patched.read_text(encoding="utf-8")
    start = text.index("download_and_extract() {")
    end = text.index("\n}\n\nfor BINDIR in", start)
    function = text[start:end]

    assert "curl" not in function
    assert "--download-ollama-asset" in function
    assert "OLLAMA_VERIFIED_ASSET_DIR" in function
    assert "zstd -d --stdout" in function
    assert "curl --fail --show-error" in original.read_text(encoding="utf-8")


def test_linux_verified_asset_helper_downloads_only_pinned_release_asset(tmp_path, monkeypatch):
    tag = "v0.40.13"
    asset_name = "ollama-linux-amd64.tar.zst"
    url = f"https://github.com/ollama/ollama/releases/download/{tag}/{asset_name}"
    manifest = {
        "tag": tag,
        "assets": {
            asset_name: {"url": url, "sha256": "c" * 64, "size": 4567}
        },
    }
    monkeypatch.setenv("OLLAMA_VERIFIED_RELEASE_ASSETS_JSON", json.dumps(manifest))
    monkeypatch.setenv("OLLAMA_VERIFIED_ASSET_DIR", str(tmp_path))
    calls = []

    def fake_download(url_arg, destination, **kwargs):
        calls.append((url_arg, destination, kwargs))
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"verified payload")
        return destination

    monkeypatch.setattr(installer, "_download_once", fake_download)

    assert installer._download_ollama_asset_cli([asset_name]) == 0
    assert calls[0][0] == url
    assert calls[0][1] == tmp_path / asset_name
    assert calls[0][2]["expected_sha256"] == "c" * 64
    assert calls[0][2]["expected_size"] == 4567
    assert isinstance(calls[0][2]["opener"], urllib.request.OpenerDirector)


def test_linux_verified_asset_helper_rejects_unpinned_or_untrusted_assets(tmp_path, monkeypatch):
    monkeypatch.setenv("OLLAMA_VERIFIED_ASSET_DIR", str(tmp_path))
    monkeypatch.setenv(
        "OLLAMA_VERIFIED_RELEASE_ASSETS_JSON",
        json.dumps({"tag": "v0.40.13", "assets": {}}),
    )
    with pytest.raises(ValueError, match="exact Linux"):
        installer._download_ollama_asset_cli(["../install.sh"])
    with pytest.raises(RuntimeError, match="does not contain"):
        installer._download_ollama_asset_cli(["ollama-linux-amd64.tar.zst"])

    asset_name = "ollama-linux-amd64.tar.zst"
    monkeypatch.setenv(
        "OLLAMA_VERIFIED_RELEASE_ASSETS_JSON",
        json.dumps({
            "tag": "v0.40.13",
            "assets": {
                asset_name: {
                    "url": "https://evil.example/ollama-linux-amd64.tar.zst",
                    "sha256": "a" * 64,
                    "size": 10,
                }
            },
        }),
    )
    with pytest.raises(RuntimeError, match="does not match the official GitHub release"):
        installer._download_ollama_asset_cli([asset_name])


def test_linux_installer_patch_fails_closed_when_upstream_function_changes(tmp_path):
    original = tmp_path / "changed-install.sh"
    original.write_text("download_assets() { echo changed; }\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="unknown|ناشناخته"):
        installer._patch_linux_install_script(original)
