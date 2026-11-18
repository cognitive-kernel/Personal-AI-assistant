from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]


def _read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_runtime_bootstrap_pins_verified_uv_assets_for_supported_platforms():
    windows = _read("bootstrap.ps1")
    unix = _read("bootstrap.sh")

    assert '$UvVersion = "0.13.9"' in windows
    assert 'UV_VERSION="0.13.9"' in unix
    assert "Get-FileHash -LiteralPath $archive -Algorithm SHA256" in windows
    assert "sha256sum" in unix and "shasum -a 256" in unix
    assert "openssl dgst -sha256" in unix
    assert "https://github.com/astral-sh/uv/releases/download/" in windows
    assert "https://github.com/astral-sh/uv/releases/download/" in unix
    assert "x86_64-pc-windows-msvc" in windows
    assert "aarch64-pc-windows-msvc" in windows
    assert "x86_64-apple-darwin" in unix
    assert "aarch64-apple-darwin" in unix
    assert "x86_64-unknown-linux-gnu" in unix
    assert "x86_64-unknown-linux-musl" in unix
    assert "aarch64-unknown-linux-gnu" in unix
    assert "aarch64-unknown-linux-musl" in unix
    assert "Unsupported platform or architecture" in unix
    assert "Unsupported Windows processor architecture" in windows
    # The managed Python 3.11 distributions used by uv do not cover these
    # uv executable-only targets; fail closed rather than claim bootstrap support.
    for unsupported in (
        "i686-unknown-linux-gnu",
        "i686-unknown-linux-musl",
        "armv7-unknown-linux-musleabihf",
        "arm-unknown-linux-musleabihf",
        "riscv64gc-unknown-linux-musl",
    ):
        assert unsupported not in unix


def test_runtime_bootstrap_uses_only_project_relative_runtime_and_preserves_invalid_venv():
    windows = _read("bootstrap.ps1")
    unix = _read("bootstrap.sh")
    command = _read("bootstrap.cmd")

    for source in (windows, unix):
        assert ".runtime" in source
        assert "UV_PYTHON_INSTALL_DIR" in source
        assert "UV_CACHE_DIR" in source
        assert "UV_PYTHON_PREFERENCE" in source
        assert "python install 3.11" in source
        assert "venv --seed --python 3.11" in source
        assert "PAA_VENV_DIR" in source
        assert "run.py" in source

    assert "Test-UsablePythonEnvironment" in windows
    assert "The existing project and managed virtual environments are not usable" in windows
    assert "test_python_environment" in unix
    assert "The existing project and managed virtual environments are not usable" in unix
    assert "Move-Item -LiteralPath $stagedUv -Destination $UvPath -Force" in windows
    assert "[switch]$Dev" in windows
    assert "[switch]$CheckOnly" in windows
    assert "[switch]$RuntimeOnly" in windows
    assert "if ($RuntimeOnly)" in windows
    assert '"--runtime-only"' in unix
    assert "%~dp0" in command
    assert "bootstrap.ps1" in command


def test_run_py_uses_a_configurable_project_virtual_environment_without_changing_default():
    source = _read("run.py")

    assert 'os.environ.get("PAA_VENV_DIR", "").strip()' in source
    assert 'VENV_DIR = Path(_configured_venv).expanduser() if _configured_venv else ROOT / ".venv"' in source
    assert "return VENV_DIR /" in source
    assert "(VENV_DIR)" in source


def test_gitignore_excludes_the_downloaded_local_runtime():
    source = _read(".gitignore")
    assert "/.runtime/" in source
