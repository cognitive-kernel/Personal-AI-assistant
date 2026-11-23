"""Capability-based discovery for supported Windows PowerShell hosts.

The application does not require a particular PowerShell major version. It
prefers a verified PowerShell Core host, falls back to Windows PowerShell, and
never treats an executable name or PATH entry as proof of compatibility.
"""
from __future__ import annotations

import os
import platform
import re
import shutil
import subprocess
from pathlib import Path
from typing import Callable, Mapping, Sequence


def windows_powershell_candidates(
    *,
    system: str | None = None,
    which: Callable[[str], str | None] | None = None,
    environ: Mapping[str, str] | None = None,
) -> list[str]:
    """Discover PowerShell executables without pinning an installation version/path."""
    system = system or platform.system()
    if system != "Windows":
        return []

    which = which or shutil.which
    environ = environ or os.environ
    candidates: list[str] = []

    def add(candidate: str | os.PathLike[str] | None) -> None:
        if not candidate:
            return
        value = os.path.abspath(os.fspath(candidate))
        key = os.path.normcase(value)
        if os.path.isfile(value) and all(os.path.normcase(item) != key for item in candidates):
            candidates.append(value)

    # Prefer PowerShell Core, then the inbox Windows PowerShell compatibility host.
    for command_name in ("pwsh.exe", "pwsh", "powershell.exe", "powershell"):
        try:
            add(which(command_name))
        except (OSError, ValueError):
            continue

    # Discover all registered PowerShell Core installations. Do not assume
    # the current major version is installed in a directory named "7".
    try:
        import winreg  # type: ignore[import-not-found]
    except ImportError:
        winreg = None

    if winreg is not None:
        registry_locations: list[tuple[tuple[int, ...], str]] = []
        views = [getattr(winreg, "KEY_WOW64_64KEY", 0), getattr(winreg, "KEY_WOW64_32KEY", 0)]
        for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
            for view in dict.fromkeys(views):
                try:
                    with winreg.OpenKey(
                        hive,
                        r"SOFTWARE\Microsoft\PowerShellCore\InstalledVersions",
                        0,
                        winreg.KEY_READ | view,
                    ) as root:
                        count = winreg.QueryInfoKey(root)[0]
                        for index in range(count):
                            try:
                                subkey_name = winreg.EnumKey(root, index)
                                with winreg.OpenKey(root, subkey_name) as entry:
                                    install_location = winreg.QueryValueEx(entry, "InstallLocation")[0]
                                    semantic_version = str(
                                        winreg.QueryValueEx(entry, "SemanticVersion")[0]
                                    )
                                version_match = re.match(r"^(\d+(?:\.\d+)*)", semantic_version)
                                version = tuple(int(part) for part in version_match.group(1).split(".")) if version_match else ()
                                if isinstance(install_location, str) and install_location.strip():
                                    registry_locations.append((version, install_location.strip()))
                            except (OSError, ValueError, TypeError):
                                continue
                except OSError:
                    continue
        for _version, install_location in sorted(registry_locations, reverse=True):
            add(Path(install_location) / "pwsh.exe")

    # Windows PowerShell is an OS component. Sysnative avoids WOW64
    # redirection when the application itself is 32-bit on 64-bit Windows.
    windir = environ.get("WINDIR") or environ.get("SystemRoot")
    if windir:
        for system_directory in ("Sysnative", "System32", "SysWOW64"):
            add(Path(windir) / system_directory / "WindowsPowerShell" / "v1.0" / "powershell.exe")

    return candidates


def find_compatible_powershell(
    candidates: Sequence[str],
    *,
    run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    cwd: str | os.PathLike[str] | None = None,
) -> tuple[str, str] | None:
    """Return an installed engine after probing its actual edition and version.

    Supported contracts are Windows PowerShell Desktop 5.1+ and PowerShell
    Core 7.0+. Future releases remain eligible when their runtime reports the
    same compatible edition contract.
    """
    probe = (
        "$edition = [string]$PSVersionTable.PSEdition; "
        "$version = [version]$PSVersionTable.PSVersion; "
        "if (($edition -eq 'Desktop' -and $version -ge [version]'5.1') -or "
        "($edition -eq 'Core' -and $version -ge [version]'7.0')) { "
        "Write-Output ('PAA_PS|' + $edition + '|' + $version.ToString()) "
        "} else { exit 41 }"
    )
    report_pattern = re.compile(
        r"^PAA_PS\|(Desktop|Core)\|([0-9]+\.[0-9]+(?:\.[0-9]+){0,2})$"
    )
    for executable in candidates:
        try:
            result = run(
                [
                    executable,
                    "-NoLogo",
                    "-NoProfile",
                    "-NonInteractive",
                    "-Command",
                    probe,
                ],
                cwd=cwd,
                text=True,
                encoding="utf-8",
                errors="replace",
                capture_output=True,
                timeout=6,
                check=False,
                shell=False,
            )
        except (OSError, subprocess.SubprocessError):
            continue
        if result.returncode != 0:
            continue
        for line in (result.stdout or "").splitlines():
            match = report_pattern.fullmatch(line.strip())
            if match:
                return executable, f"{match.group(1)} {match.group(2)}"
    return None
