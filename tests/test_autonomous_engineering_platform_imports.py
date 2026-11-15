from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def test_progress_and_process_group_modules_import_without_psutil():
    repository_root = Path(__file__).resolve().parents[1]
    probe = r"""
import builtins

original_import = builtins.__import__

def reject_psutil(name, *args, **kwargs):
    if name == "psutil" or name.startswith("psutil."):
        raise ModuleNotFoundError("psutil intentionally unavailable in import-isolation probe")
    return original_import(name, *args, **kwargs)

builtins.__import__ = reject_psutil

import scripts.autonomous_engineering_process_group
import scripts.autonomous_engineering_codex_runner
import scripts.autonomous_engineering_progress
"""

    result = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=repository_root,
        capture_output=True,
        text=True,
        check=False,
        timeout=15,
    )

    assert result.returncode == 0, (
        "Windows-safe controller imports must not require the POSIX-only psutil "
        f"runtime path. stdout={result.stdout!r}; stderr={result.stderr!r}"
    )
