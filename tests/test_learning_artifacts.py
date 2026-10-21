import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_generated_learning_artifacts_match_canonical_source():
    result = subprocess.run(
        [sys.executable, "scripts/generate_learning_artifacts.py", "--check"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "up to date" in result.stdout
