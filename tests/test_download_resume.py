import importlib.util
import json
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


@pytest.mark.parametrize(
    ("existing_bytes", "content_range"),
    [
        (b"", "bytes 0-4/10"),
        (b"abc", "bytes 0-4/10"),
        (b"abc", "not-a-content-range"),
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
