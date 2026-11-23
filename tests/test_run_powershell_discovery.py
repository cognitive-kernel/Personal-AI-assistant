from __future__ import annotations

import subprocess
import run


def test_powershell_discovery_prefers_a_verified_powerShell_7_runtime(monkeypatch):
    candidates = {
        "pwsh.exe": r"C:\Program Files\PowerShell\7\pwsh.exe",
        "pwsh": None,
        "powershell.exe": r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe",
        "powershell": None,
    }
    monkeypatch.setattr(run.shutil, "which", lambda name: candidates.get(name))
    calls: list[list[str]] = []

    def fake_run(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(
            command,
            0,
            "PowerShell startup banner\nPAA_PS|Core|7.4.2\n",
            "",
        )

    monkeypatch.setattr(run.subprocess, "run", fake_run)

    assert run._find_compatible_powershell() == (
        candidates["pwsh.exe"],
        "Core 7.4.2",
    )
    assert len(calls) == 1
    assert calls[0][0] == candidates["pwsh.exe"]
    assert calls[0][1:4] == ["-NoLogo", "-NoProfile", "-NonInteractive"]
    assert calls[0][4] == "-Command"
    assert "-ExecutionPolicy" not in calls[0]


def test_powershell_discovery_falls_back_to_windows_powershell_5_1(monkeypatch):
    candidates = {
        "pwsh.exe": r"C:\Program Files\PowerShell\7\pwsh.exe",
        "pwsh": None,
        "powershell.exe": r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe",
        "powershell": None,
    }
    monkeypatch.setattr(run.shutil, "which", lambda name: candidates.get(name))
    calls: list[str] = []

    def fake_run(command, **kwargs):
        calls.append(command[0])
        if command[0] == candidates["pwsh.exe"]:
            return subprocess.CompletedProcess(
                command, 41, "PAA_PS|Core|6.2.0\n", "unsupported PowerShell Core version"
            )
        return subprocess.CompletedProcess(command, 0, "PAA_PS|Desktop|5.1.26100.1\n", "")

    monkeypatch.setattr(run.subprocess, "run", fake_run)

    assert run._find_compatible_powershell() == (
        candidates["powershell.exe"],
        "Desktop 5.1.26100.1",
    )
    assert calls == [candidates["pwsh.exe"], candidates["powershell.exe"]]


def test_powershell_discovery_skips_broken_candidate_and_checks_the_next(monkeypatch):
    candidates = {
        "pwsh.exe": r"C:\Program Files\PowerShell\7\pwsh.exe",
        "pwsh": None,
        "powershell.exe": r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe",
        "powershell": None,
    }
    monkeypatch.setattr(run.shutil, "which", lambda name: candidates.get(name))
    calls: list[str] = []

    def fake_run(command, **kwargs):
        calls.append(command[0])
        if command[0] == candidates["pwsh.exe"]:
            raise OSError("runtime could not start")
        return subprocess.CompletedProcess(command, 0, "PAA_PS|Desktop|5.1.0\n", "")

    monkeypatch.setattr(run.subprocess, "run", fake_run)

    assert run._find_compatible_powershell() == (
        candidates["powershell.exe"],
        "Desktop 5.1.0",
    )
    assert len(calls) == 2


def test_powershell_discovery_returns_unavailable_when_no_supported_engine_exists(monkeypatch):
    monkeypatch.setattr(run.shutil, "which", lambda _name: None)
    monkeypatch.setattr(run.platform, "system", lambda: "Linux")

    assert run._powershell_candidates() == []
    assert run._find_compatible_powershell() is None


def test_powershell_discovery_rejects_unrecognized_probe_output(monkeypatch):
    monkeypatch.setattr(
        run.shutil,
        "which",
        lambda name: r"C:\Program Files\PowerShell\7\pwsh.exe" if name == "pwsh.exe" else None,
    )
    monkeypatch.setattr(
        run.subprocess,
        "run",
        lambda command, **kwargs: subprocess.CompletedProcess(
            command, 0, "Core 7.4.2\n", ""
        ),
    )

    assert run._find_compatible_powershell() is None


def test_powershell_discovery_uses_bounded_noninteractive_probe(monkeypatch):
    executable = r"C:\Program Files\PowerShell\7\pwsh.exe"
    monkeypatch.setattr(
        run.shutil,
        "which",
        lambda name: executable if name == "pwsh.exe" else None,
    )
    captured = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        return subprocess.CompletedProcess(command, 0, "PAA_PS|Core|7.5.0\n", "")

    monkeypatch.setattr(run.subprocess, "run", fake_run)

    assert run._find_compatible_powershell() == (executable, "Core 7.5.0")
    assert captured["kwargs"]["timeout"] == 6
    assert captured["kwargs"]["shell"] is False
    assert captured["kwargs"]["check"] is False
