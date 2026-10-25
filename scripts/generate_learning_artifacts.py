"""Generate checked-in learning review prompts from one source."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "docs" / "learning" / "learning-artifacts.json"


def render() -> dict[str, str]:
    payload = json.loads(SOURCE.read_text(encoding="utf-8"))
    if payload.get("schema_version") != 1:
        raise ValueError("Unsupported learning artifact source schema_version")
    files = payload.get("generated_files")
    if not isinstance(files, dict) or not files:
        raise ValueError("generated_files must be a non-empty object")
    for relative, content in files.items():
        path = Path(relative)
        if path.is_absolute() or ".." in path.parts or not isinstance(content, str):
            raise ValueError(f"Unsafe or invalid generated artifact entry: {relative!r}")
    return files


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="fail if generated files are stale")
    args = parser.parse_args()
    mismatches: list[str] = []
    for relative, expected in render().items():
        target = ROOT / relative
        current = target.read_text(encoding="utf-8") if target.exists() else None
        if current != expected:
            if args.check:
                mismatches.append(relative)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(expected, encoding="utf-8", newline="\n")
                print(f"generated {relative}")
    if mismatches:
        print("Stale generated learning artifacts: " + ", ".join(mismatches), file=sys.stderr)
        print("Run: python scripts/generate_learning_artifacts.py", file=sys.stderr)
        return 1
    if args.check:
        print("Learning artifacts are up to date.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
