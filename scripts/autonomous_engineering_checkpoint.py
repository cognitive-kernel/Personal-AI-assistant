from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import psutil

ROOT = Path(__file__).resolve().parents[1]
POLL_SECONDS = 20


def store_dir() -> Path:
    configured = os.getenv("AUTONOMOUS_ENGINEERING_PROGRESS_DIR", "").strip()
    if configured:
        return Path(configured).expanduser()
    program_data = os.getenv("PROGRAMDATA")
    if program_data:
        return Path(program_data) / "PersonalAIAssistant" / "autonomous-engineering"
    return Path.home() / ".personal-ai-assistant" / "autonomous-engineering"


def paths() -> dict[str, Path]:
    directory = store_dir()
    return {
        "directory": directory,
        "patch": directory / "worktree-checkpoint.patch",
        "metadata": directory / "worktree-checkpoint.json",
        "plan": directory / "worktree-plan.json",
        "stop": directory / "worktree-checkpoint.stop",
    }


def git(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        ["git", *args], cwd=ROOT, capture_output=True, text=True,
        encoding="utf-8", errors="replace", check=False,
    )
    if check and result.returncode:
        raise RuntimeError((result.stderr or result.stdout or "Git checkpoint command failed")[-3000:])
    return result


def stop_existing_watcher(metadata: dict[str, Any] | None) -> None:
    if not isinstance(metadata, dict):
        return
    pid = metadata.get("watcher_pid")
    if not isinstance(pid, int) or pid == os.getpid():
        return
    try:
        process = psutil.Process(pid)
        command = " ".join(process.cmdline()).casefold()
        if "autonomous_engineering_checkpoint.py" in command and "watch" in command:
            process.terminate()
            try:
                process.wait(timeout=3)
            except psutil.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)
    except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
        return


def read_metadata() -> dict[str, Any] | None:
    target = paths()["metadata"]
    try:
        value = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def write_snapshot(base_sha: str, run_id: str, task: int, watcher_pid: int | None = None) -> None:
    state = paths()
    state["directory"].mkdir(parents=True, exist_ok=True)
    git("add", "-N", "-A", "--", "app", "tests", "docs", "scripts")
    result = git(
        "diff", "--binary", base_sha, "--",
        "app", "tests", "docs", "scripts",
        ":!scripts/autonomous_engineering_cycle.py",
        ":!scripts/autonomous_engineering_checkpoint.py",
        ":!scripts/autonomous_engineering_progress.py",
    )
    previous = read_metadata()
    if not result.stdout and state["patch"].is_file() and state["patch"].stat().st_size > 0:
        if previous and previous.get("base_sha") == base_sha:
            return
    patch_tmp = state["patch"].with_suffix(".patch.tmp")
    patch_tmp.write_text(result.stdout, encoding="utf-8", newline="")
    patch_tmp.replace(state["patch"])
    plan = ROOT / ".agent-progress.json"
    if plan.is_file():
        plan_tmp = state["plan"].with_suffix(".json.tmp")
        plan_tmp.write_bytes(plan.read_bytes())
        plan_tmp.replace(state["plan"])
    metadata = {
        "schema_version": 1,
        "base_sha": base_sha,
        "run_id": run_id,
        "task": task,
        "watcher_pid": watcher_pid,
        "updated_at": time.time(),
        "patch_bytes": len(result.stdout.encode("utf-8")),
    }
    metadata_tmp = state["metadata"].with_suffix(".json.tmp")
    metadata_tmp.write_text(json.dumps(metadata, ensure_ascii=False) + "\n", encoding="utf-8")
    metadata_tmp.replace(state["metadata"])


def watch(base_sha: str, run_id: str, task: int, interval: int) -> int:
    state = paths()
    state["directory"].mkdir(parents=True, exist_ok=True)
    state["stop"].unlink(missing_ok=True)
    while not state["stop"].exists():
        try:
            write_snapshot(base_sha, run_id, task, os.getpid())
        except (OSError, RuntimeError) as exc:
            print(f"Checkpoint snapshot skipped: {type(exc).__name__}: {exc}", file=sys.stderr)
        time.sleep(max(5, min(interval, 120)))
    return 0


def restore(base_sha: str) -> bool:
    state = paths()
    metadata = read_metadata()
    stop_existing_watcher(metadata)
    state["stop"].write_text("stop\n", encoding="utf-8")
    if not metadata or not state["patch"].is_file():
        return False
    current_head = git("rev-parse", "HEAD").stdout.strip()
    if current_head != base_sha or metadata.get("base_sha") != base_sha:
        raise RuntimeError(
            "A durable worktree checkpoint exists but its base SHA differs from current main; "
            "the checkpoint was preserved and must not be applied to a different base."
        )
    patch = state["patch"].read_text(encoding="utf-8")
    if not patch.strip():
        if state["plan"].is_file():
            (ROOT / ".agent-progress.json").write_bytes(state["plan"].read_bytes())
        return False
    check = subprocess.run(
        ["git", "apply", "--check", "--binary"],
        input=patch, cwd=ROOT, capture_output=True, text=True,
        encoding="utf-8", errors="replace", check=False,
    )
    if check.returncode:
        raise RuntimeError("The saved worktree checkpoint no longer applies cleanly; it was preserved.")
    apply = subprocess.run(
        ["git", "apply", "--binary"],
        input=patch, cwd=ROOT, capture_output=True, text=True,
        encoding="utf-8", errors="replace", check=False,
    )
    if apply.returncode:
        raise RuntimeError("Could not restore the saved worktree checkpoint; it was preserved.")
    if state["plan"].is_file():
        (ROOT / ".agent-progress.json").write_bytes(state["plan"].read_bytes())
    state["stop"].unlink(missing_ok=True)
    return True


def snapshot(base_sha: str, run_id: str, task: int) -> None:
    write_snapshot(base_sha, run_id, task, None)


def stop() -> None:
    state = paths()
    stop_existing_watcher(read_metadata())
    state["directory"].mkdir(parents=True, exist_ok=True)
    state["stop"].write_text("stop\n", encoding="utf-8")


def clear() -> None:
    state = paths()
    stop_existing_watcher(read_metadata())
    state["stop"].write_text("stop\n", encoding="utf-8")
    for key in ("patch", "metadata", "plan"):
        state[key].unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    subs = parser.add_subparsers(dest="command", required=True)
    watcher = subs.add_parser("watch")
    watcher.add_argument("--base-sha", required=True)
    watcher.add_argument("--run-id", required=True)
    watcher.add_argument("--task", required=True, type=int)
    watcher.add_argument("--interval", type=int, default=POLL_SECONDS)
    restore_parser = subs.add_parser("restore")
    restore_parser.add_argument("--base-sha", required=True)
    snapshot_parser = subs.add_parser("snapshot")
    snapshot_parser.add_argument("--base-sha", required=True)
    snapshot_parser.add_argument("--run-id", required=True)
    snapshot_parser.add_argument("--task", required=True, type=int)
    subs.add_parser("stop")
    subs.add_parser("clear")
    args = parser.parse_args()
    if args.command == "watch":
        return watch(args.base_sha, args.run_id, args.task, args.interval)
    if args.command == "restore":
        print("restored" if restore(args.base_sha) else "no-checkpoint")
        return 0
    if args.command == "snapshot":
        snapshot(args.base_sha, args.run_id, args.task)
        return 0
    if args.command == "stop":
        stop()
        return 0
    clear()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"Autonomous checkpoint failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(1)
