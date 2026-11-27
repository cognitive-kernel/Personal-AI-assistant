from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_run_py_help_exits_without_bootstrap_or_web_server():
    environment = os.environ.copy()
    environment.pop("PAA_VENV_DIR", None)
    result = subprocess.run(
        [sys.executable, str(ROOT / "run.py"), "--help"],
        cwd=ROOT,
        env=environment,
        text=True,
        capture_output=True,
        timeout=10,
        check=False,
    )

    assert result.returncode == 0
    assert "Usage: python run.py" in result.stdout
    assert "--check-only" in result.stdout
    assert "Uvicorn running" not in result.stdout
    assert "ساخت محیط مجازی پروژه" not in result.stdout
    assert result.stderr == ""
