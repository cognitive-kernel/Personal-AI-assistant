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
    """Discover Core first, then Desktop, without pinning a major version."""
    system = system or platform.system()
    if system != "Windows":
        return []

    which = which or shutil.which
    environ = environ or os.environ
    core_candidates: list[str] = []
    desktop_candidates: list[str] = []

    def add(
        target: list[str],
        candidate: str | os.PathLike[str] | None,
        *,
        require_file: bool = True,
    ) -> None:
        if not candidate:
            return
        raw = os.fspath(candidate)
        # Preserve Windows absolute paths when this module is exercised by
        # cross-platform tests; resolve relative candidates against this host.
        is_windows_absolute = (
            (len(raw) > 2 and raw[1] == ":" and raw[2] in (chr(92), "/"))
            or raw.startswith(chr(92) * 2)
        )
        value = raw if is_windows_absolute else os.path.abspath(raw)
        key = os.path.normcase(value)
        if (
            (not require_file or os.path.isfile(value))
            and all(os.path.normcase(item) != key for item in core_candidates + desktop_candidates)
        ):
            target.append(value)

    # PATH is searched separately by edition so a Desktop host on PATH cannot
    # outrank a Core host registered at a custom installation location.
    for command_name in ("pwsh.exe", "pwsh"):
        try:
            add(core_candidates, which(command_name), require_file=False)
        except (OSError, ValueError):
            continue

    # Discover every registered PowerShell Core installation. Do not assume
    # the current major version is installed in a directory named "7".
    try:
        import winreg  # type: ignore[import-not-found]
    except ImportError:
        winreg = None

    if winreg is not None:
        registry_locations: list[tuple[tuple[int, ...], str]] = []
        views = [
            getattr(winreg, "KEY_WOW64_64KEY", 0),
            getattr(winreg, "KEY_WOW64_32KEY", 0),
        ]
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
                                version = (
                                    tuple(int(part) for part in version_match.group(1).split("."))
                                    if version_match else ()
                                )
                                if isinstance(install_location, str) and install_location.strip():
                                    registry_locations.append((version, install_location.strip()))
                            except (OSError, ValueError, TypeError):
                                continue
                except OSError:
                    continue
        for _version, install_location in sorted(registry_locations, reverse=True):
            add(core_candidates, Path(install_location) / "pwsh.exe")

    for command_name in ("powershell.exe", "powershell"):
        try:
            add(desktop_candidates, which(command_name), require_file=False)
        except (OSError, ValueError):
            continue

    # Windows PowerShell is an OS component. Sysnative avoids WOW64
    # redirection when the application itself is 32-bit on 64-bit Windows.
    windir = environ.get("WINDIR") or environ.get("SystemRoot")
    if windir:
        for system_directory in ("Sysnative", "System32", "SysWOW64"):
            add(
                desktop_candidates,
                Path(windir) / system_directory / "WindowsPowerShell" / "v1.0" / "powershell.exe",
            )

    return core_candidates + desktop_candidates


def find_compatible_powershell(
    candidates: Sequence[str],
    *,
    run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    cwd: str | os.PathLike[str] | None = None,
) -> tuple[str, str] | None:
    """Return a host only when the Windows capabilities required by Broker exist.

    Edition and version are reported for diagnostics, not used as a compatibility
    gate. A host that provides the required modules, cmdlets, registry API and
    Windows tools is eligible regardless of its numeric major/minor version.
    """
    probe = (
        "$edition = [string]$PSVersionTable.PSEdition; "
        "$version = [string]$PSVersionTable.PSVersion; "
        "$previousModulePath = $env:PSModulePath; "
        "try { "
        "$env:PSModulePath = Join-Path $PSHOME 'Modules'; "
        "Import-Module Microsoft.PowerShell.Management -ErrorAction Stop; "
        "Import-Module Microsoft.PowerShell.Utility -ErrorAction Stop; "
        "Import-Module Microsoft.PowerShell.Security -ErrorAction Stop; "
        "Import-Module CimCmdlets -ErrorAction Stop; "
        "} catch { exit 42 } finally { $env:PSModulePath = $previousModulePath }; "
        "$required = @('Get-Acl','Set-Acl','Get-Service','New-Service','Stop-Service',"
        "'Start-Service','Get-CimInstance','Get-FileHash','Start-Process','Get-Process'); "
        "$missing = @($required | Where-Object { -not (Get-Command $_ -ErrorAction SilentlyContinue) }); "
        "$startProcess = Get-Command Start-Process -ErrorAction SilentlyContinue; "
        "$hostPath = [string](Get-Process -Id $PID -ErrorAction Stop).Path; "
        "$registry = [Microsoft.Win32.RegistryKey]::OpenBaseKey("
        "[Microsoft.Win32.RegistryHive]::LocalMachine,[Microsoft.Win32.RegistryView]::Registry32); "
        "$registry.Close(); "
        "$missingTools = @('sc.exe','icacls.exe' | Where-Object { -not (Get-Command $_ -ErrorAction SilentlyContinue) }); "
        "if (($edition -eq 'Desktop' -or $edition -eq 'Core') -and "
        "$missing.Count -eq 0 -and $missingTools.Count -eq 0 -and "
        "$startProcess.Parameters.ContainsKey('Verb') -and "
        "-not [string]::IsNullOrWhiteSpace($hostPath)) { "
        "Write-Output ('PAA_PS|' + $edition + '|' + $version) "
        "} else { exit 41 }"
    )
    report_pattern = re.compile(
        r"^PAA_PS\\|(Desktop|Core)\\|([0-9]+\\.[0-9]+(?:\\.[0-9]+){0,2})$"
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
