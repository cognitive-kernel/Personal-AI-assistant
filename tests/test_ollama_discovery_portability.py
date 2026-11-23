from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]


def _load_bootstrap():
    spec = importlib.util.spec_from_file_location("paa_run_ollama_discovery_test", ROOT / "run.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_ollama_discovery_uses_machine_install_roots_not_user_profiles(monkeypatch, tmp_path):
    bootstrap = _load_bootstrap()
    machine_root = tmp_path / "Program Files"
    user_root = tmp_path / "User" / "AppData" / "Local"
    machine_executable = machine_root / "Ollama" / "ollama.exe"
    user_executable = user_root / "Programs" / "Ollama" / "ollama.exe"
    machine_executable.parent.mkdir(parents=True)
    user_executable.parent.mkdir(parents=True)
    machine_executable.write_bytes(b"machine")
    user_executable.write_bytes(b"user")

    monkeypatch.setattr(
        bootstrap,
        "os",
        SimpleNamespace(
            name="nt",
            environ={
                "ProgramFiles": str(machine_root),
                "ProgramW6432": "",
                "LOCALAPPDATA": str(user_root),
            },
        ),
    )
    monkeypatch.setattr(bootstrap.shutil, "which", lambda _name: None)

    assert bootstrap._find_ollama() == str(machine_executable)


def test_ollama_discovery_has_no_home_or_localappdata_fallback():
    source = (ROOT / "run.py").read_text(encoding="utf-8")
    function = source[source.index("def _find_ollama()"):source.index("\ndef _ensure_ollama()")]

    assert "Path.home()" not in function
    assert "LOCALAPPDATA" not in function
    assert "ProgramFiles" in function
