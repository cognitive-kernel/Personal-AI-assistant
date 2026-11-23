from __future__ import annotations

import subprocess

import pytest

import run


def _mock_windows(monkeypatch, candidates):
    monkeypatch.setattr(run.platform, "system", lambda: "Windows")
    monkeypatch.setattr(run.shutil, "which", lambda name: candidates.get(name))


def test_powershell_discovery_prefers_a_verified_core_runtime(monkeypatch):
    candidates = {
        "pwsh.exe": r"C:\Program Files\PowerShell\8\pwsh.exe",
        "pwsh": None,
        "powershell.exe": r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe",
        "powershell": None,
    }
    _mock_windows(monkeypatch, candidates)
    calls = []

    def fake_run(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, "PowerShell startup banner\nPAA_PS|Core|8.0.1\n", "")

    monkeypatch.setattr(run.subprocess, "run", fake_run)

    assert run._find_compatible_powershell() == (candidates["pwsh.exe"], "Core 8.0.1")
    assert len(calls) == 1
    assert calls[0][0] == candidates["pwsh.exe"]
    assert calls[0][1:4] == ["-NoLogo", "-NoProfile", "-NonInteractive"]
    assert calls[0][4] == "-Command"
    probe = calls[0][-1]
    assert "Get-Acl" in probe and "Set-Acl" in probe
    assert "Get-CimInstance" in probe
    assert "sc.exe" in probe and "icacls.exe" in probe and "takeown.exe" in probe
    assert "RegistryKey" in probe
    assert "[version]'7.0'" not in probe
    assert "PSVersionTable.Prerelease" not in probe


def test_powershell_discovery_falls_back_when_core_lacks_required_capabilities(monkeypatch):
    candidates = {
        "pwsh.exe": r"C:\Program Files\PowerShell\8\pwsh.exe",
        "pwsh": None,
        "powershell.exe": r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe",
        "powershell": None,
    }
    _mock_windows(monkeypatch, candidates)
    calls = []

    def fake_run(command, **kwargs):
        calls.append(command[0])
        if command[0] == candidates["pwsh.exe"]:
            return subprocess.CompletedProcess(command, 41, "", "required Windows capability missing")
        return subprocess.CompletedProcess(command, 0, "PAA_PS|Desktop|5.1.26100.1\n", "")

    monkeypatch.setattr(run.subprocess, "run", fake_run)

    assert run._find_compatible_powershell() == (candidates["powershell.exe"], "Desktop 5.1.26100.1")
    assert calls == [candidates["pwsh.exe"], candidates["powershell.exe"]]


def test_powershell_discovery_skips_broken_candidate_and_checks_the_next(monkeypatch):
    candidates = {
        "pwsh.exe": r"C:\Program Files\PowerShell\8\pwsh.exe",
        "pwsh": None,
        "powershell.exe": r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe",
        "powershell": None,
    }
    _mock_windows(monkeypatch, candidates)
    calls = []

    def fake_run(command, **kwargs):
        calls.append(command[0])
        if command[0] == candidates["pwsh.exe"]:
            raise OSError("runtime could not start")
        return subprocess.CompletedProcess(command, 0, "PAA_PS|Desktop|5.1.0\n", "")

    monkeypatch.setattr(run.subprocess, "run", fake_run)

    assert run._find_compatible_powershell() == (candidates["powershell.exe"], "Desktop 5.1.0")
    assert len(calls) == 2


def test_powershell_discovery_returns_unavailable_when_no_supported_engine_exists(monkeypatch):
    monkeypatch.setattr(run.platform, "system", lambda: "Linux")
    monkeypatch.setattr(run.shutil, "which", lambda _name: None)

    assert run._powershell_candidates() == []
    assert run._find_compatible_powershell() is None


def test_powershell_discovery_rejects_unrecognized_probe_output(monkeypatch):
    _mock_windows(monkeypatch, {
        "pwsh.exe": r"C:\Program Files\PowerShell\8\pwsh.exe",
        "pwsh": None,
        "powershell.exe": None,
        "powershell": None,
    })
    monkeypatch.setattr(
        run.subprocess,
        "run",
        lambda command, **kwargs: subprocess.CompletedProcess(command, 0, "Core 8.0.1\n", ""),
    )

    assert run._find_compatible_powershell() is None


def test_powershell_discovery_uses_bounded_noninteractive_probe(monkeypatch):
    executable = r"C:\Program Files\PowerShell\8\pwsh.exe"
    _mock_windows(monkeypatch, {
        "pwsh.exe": executable,
        "pwsh": None,
        "powershell.exe": None,
        "powershell": None,
    })
    captured = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        return subprocess.CompletedProcess(command, 0, "PAA_PS|Core|8.0.0\n", "")

    monkeypatch.setattr(run.subprocess, "run", fake_run)

    assert run._find_compatible_powershell() == (executable, "Core 8.0.0")
    assert captured["kwargs"]["timeout"] == 6
    assert captured["kwargs"]["shell"] is False
    assert captured["kwargs"]["check"] is False


def test_powershell_discovery_uses_registered_install_locations_without_major_version_path(monkeypatch):
    import sys
    import types

    class FakeKey:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    fake_winreg = types.SimpleNamespace(
        HKEY_LOCAL_MACHINE=1,
        HKEY_CURRENT_USER=2,
        KEY_READ=0x20019,
        KEY_WOW64_64KEY=0,
        KEY_WOW64_32KEY=0,
        OpenKey=lambda _parent, path, *_args: FakeKey(),
        QueryInfoKey=lambda _key: (1, 0, 0),
        EnumKey=lambda _key, _index: "registered-install",
        QueryValueEx=lambda _key, name: (
            (r"E:\Custom\PowerShell\8.0" if name == "InstallLocation" else "8.0.0"),
            1,
        ),
    )
    monkeypatch.setitem(sys.modules, "winreg", fake_winreg)
    monkeypatch.setattr(run.platform, "system", lambda: "Windows")
    monkeypatch.setattr(run.shutil, "which", lambda name: r"C:\\Windows\\System32\\WindowsPowerShell\\v1.0\\powershell.exe" if name == "powershell.exe" else None)
    monkeypatch.setattr(
        "app.windows_powershell.os.path.isfile",
        lambda path: str(path).lower().endswith(("pwsh.exe", "powershell.exe")),
    )

    candidates = run._powershell_candidates()

    assert len(candidates) >= 2
    assert "custom" in candidates[0].lower()
    assert "8.0" in candidates[0]
    assert candidates[0].lower().endswith("pwsh.exe")
    assert candidates[1].lower().endswith("powershell.exe")


def test_powershell_discovery_accepts_older_core_when_capability_probe_succeeds(monkeypatch):
    executable = r"C:\\Program Files\\PowerShell\\6\\pwsh.exe"
    _mock_windows(monkeypatch, {
        "pwsh.exe": executable,
        "pwsh": None,
        "powershell.exe": None,
        "powershell": None,
    })
    captured = {}

    def fake_run(command, **kwargs):
        captured["probe"] = command[-1]
        return subprocess.CompletedProcess(command, 0, "PAA_PS|Core|6.2.0\n", "")

    monkeypatch.setattr(run.subprocess, "run", fake_run)

    assert run._find_compatible_powershell() == (executable, "Core 6.2.0")
    assert "[version]'7.0'" not in captured["probe"]
    assert "PSVersionTable.Prerelease" not in captured["probe"]


def test_windows_discovery_supports_a_desktop_only_host(monkeypatch):
    desktop = r"C:\\Windows\\System32\\WindowsPowerShell\\v1.0\\powershell.exe"
    monkeypatch.setattr(run.platform, "system", lambda: "Windows")
    monkeypatch.setattr(
        run.shutil,
        "which",
        lambda name: desktop if name in ("powershell.exe", "powershell") else None,
    )
    monkeypatch.setattr(
        run.subprocess,
        "run",
        lambda command, **kwargs: subprocess.CompletedProcess(
            command, 0, "PAA_PS|Desktop|5.1.26100.1\n", ""
        ),
    )

    assert run._find_compatible_powershell() == (desktop, "Desktop 5.1.26100.1")


def test_windows_discovery_supports_a_core_only_host(monkeypatch):
    core = r"C:\\Custom\\PowerShell\\8.0\\pwsh.exe"
    monkeypatch.setattr(run.platform, "system", lambda: "Windows")
    monkeypatch.setattr(
        run.shutil,
        "which",
        lambda name: core if name in ("pwsh.exe", "pwsh") else None,
    )
    monkeypatch.setattr(
        run.subprocess,
        "run",
        lambda command, **kwargs: subprocess.CompletedProcess(
            command, 0, "PAA_PS|Core|8.0.1\n", ""
        ),
    )

    assert run._find_compatible_powershell() == (core, "Core 8.0.1")


def test_windows_discovery_reports_no_host_when_no_supported_shell_exists(monkeypatch):
    import sys

    monkeypatch.setitem(sys.modules, "winreg", None)
    monkeypatch.setattr(run.platform, "system", lambda: "Windows")
    monkeypatch.setattr(run.shutil, "which", lambda _name: None)
    monkeypatch.setattr(
        "app.windows_powershell.os.path.isfile",
        lambda _path: False,
    )
    monkeypatch.setattr("app.windows_powershell.os.environ", {})

    assert run._powershell_candidates() == []
    assert run._find_compatible_powershell() is None


@pytest.mark.skipif(run.platform.system() != "Windows", reason="requires a real Windows host")
def test_windows_discovery_selects_an_installed_compatible_host():
    selected = run._find_compatible_powershell()

    assert selected is not None
    assert selected[1].startswith(("Core ", "Desktop "))
