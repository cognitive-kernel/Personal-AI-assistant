from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import psutil

try:
    from .autonomous_engineering_checkpoint_contract import (
        SCHEMA_VERSION,
        build_checkpoint_metadata,
    )
except ImportError:  # Direct script execution from the scripts directory.
    from autonomous_engineering_checkpoint_contract import (
        SCHEMA_VERSION,
        build_checkpoint_metadata,
    )

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


def _identity(run_id: str, task: int) -> str:
    safe_run = str(run_id)
    if (
        not safe_run or len(safe_run) > 100
        or any(not (ch.isalnum() or ch in "-_") for ch in safe_run)
        or task < 1
    ):
        raise ValueError("Invalid checkpoint run/task identity")
    return f"{safe_run}-{task}"


def paths(run_id: str, task: int) -> dict[str, Path]:
    directory = store_dir()
    identity = _identity(run_id, task)
    stem = f"worktree-checkpoint-{identity}"
    return {
        "directory": directory,
        "patch": directory / f"{stem}.patch",
        "metadata": directory / f"{stem}.json",
        "plan": directory / f"worktree-plan-{identity}.json",
        "stop": directory / f"{stem}.stop",
    }


def watcher_marker_paths(run_id: str, task: int) -> tuple[Path, Path]:
    state = paths(run_id, task)
    stem = state["metadata"].stem
    return (state["directory"] / f"{stem}.ready", state["directory"] / f"{stem}.error")


def git(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        ["git", *args], cwd=ROOT, capture_output=True, text=True,
        encoding="utf-8", errors="replace", check=False,
    )
    if check and result.returncode:
        raise RuntimeError((result.stderr or result.stdout or "Git checkpoint command failed")[-3000:])
    return result


def _atomic_write(path: Path, data: str | bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f".{os.getpid()}.tmp")
    if isinstance(data, bytes):
        temporary.write_bytes(data)
    else:
        temporary.write_text(data, encoding="utf-8", newline="")
    temporary.replace(path)


def stop_existing_watcher(metadata: dict[str, Any] | None) -> None:
    if not isinstance(metadata, dict):
        return
    pid = metadata.get("watcher_pid")
    expected_run = metadata.get("run_id")
    expected_task = metadata.get("task")
    if not isinstance(pid, int) or pid <= 0 or pid == os.getpid():
        return
    try:
        process = psutil.Process(pid)
        command = process.cmdline()
        command_text = " ".join(command).casefold()
        if (
            "autonomous_engineering_checkpoint.py" in command_text
            and "watch" in command_text
            and expected_run is not None
            and str(expected_run) in command
            and expected_task is not None
            and str(expected_task) in command
        ):
            process.terminate()
            try:
                process.wait(timeout=3)
            except psutil.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)
    except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
        return


def read_metadata(run_id: str, task: int) -> dict[str, Any] | None:
    try:
        value = json.loads(paths(run_id, task)["metadata"].read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def write_snapshot(base_sha: str, run_id: str, task: int, watcher_pid: int | None = None) -> None:
    state = paths(run_id, task)
    state["directory"].mkdir(parents=True, exist_ok=True)
    git("add", "-N", "-A", "--", "app", "tests", "docs", "scripts")
    result = git(
        "diff", "--binary", base_sha, "--",
        "app", "tests", "docs", "scripts",
        ":!scripts/autonomous_engineering_cycle.py",
        ":!scripts/autonomous_engineering_checkpoint.py",
        ":!scripts/autonomous_engineering_progress.py",
        ":!scripts/autonomous_engineering_state.py",
    )
    previous = read_metadata(run_id, task)
    preserve_existing_patch = (
        not result.stdout
        and state["patch"].is_file()
        and state["patch"].stat().st_size > 0
        and previous
        and previous.get("base_sha") == base_sha
        and previous.get("run_id") == run_id
        and previous.get("task") == task
    )
    if not preserve_existing_patch:
        _atomic_write(state["patch"], result.stdout)
    plan = ROOT / ".agent-progress.json"
    if plan.is_file():
        _atomic_write(state["plan"], plan.read_bytes())
    metadata = build_checkpoint_metadata(
        base_sha=base_sha,
        run_id=run_id,
        task=task,
        watcher_pid=watcher_pid,
        updated_at=time.time(),
        patch_bytes=state["patch"].stat().st_size if state["patch"].exists() else 0,
    )
    # This atomic metadata replacement is the readiness commit point. The
    # controller must not depend on a second, separately published marker.
    _atomic_write(state["metadata"], json.dumps(metadata, ensure_ascii=False, sort_keys=True) + "\n")


def watch(base_sha: str, run_id: str, task: int, interval: int) -> int:
    state = paths(run_id, task)
    ready_path, error_path = watcher_marker_paths(run_id, task)
    state["directory"].mkdir(parents=True, exist_ok=True)
    # A watcher owns only its own stop/readiness/error markers and never clears another run's state.
    state["stop"].unlink(missing_ok=True)
    ready_path.unlink(missing_ok=True)
    error_path.unlink(missing_ok=True)
    try:
        write_snapshot(base_sha, run_id, task, os.getpid())
        # The atomic metadata replacement above is the only readiness signal.
        # Do not perform a second synchronous marker write here: a filesystem
        # stall must not block the watcher after its durable snapshot is saved.
    except Exception as exc:
        try:
            _atomic_write(error_path, f"{type(exc).__name__}: {exc}\n")
        except OSError as marker_exc:
            print(
                f"Watcher startup failed ({type(exc).__name__}: {exc}); "
                f"error marker also failed ({type(marker_exc).__name__}: {marker_exc})",
                file=sys.stderr,
            )
        raise
    while not state["stop"].exists():
        try:
            write_snapshot(base_sha, run_id, task, os.getpid())
        except (OSError, RuntimeError) as exc:
            print(f"Checkpoint snapshot skipped: {type(exc).__name__}: {exc}", file=sys.stderr)
        time.sleep(max(5, min(interval, 120)))
    return 0


def _latest_checkpoint() -> tuple[dict[str, Any], dict[str, Path]] | None:
    directory = store_dir()
    # One-time, lock-protected migration from the pre-isolation shared checkpoint format.
    legacy_metadata_path = directory / "worktree-checkpoint.json"
    legacy_patch_path = directory / "worktree-checkpoint.patch"
    legacy_plan_path = directory / "worktree-plan.json"
    if legacy_metadata_path.is_file() and legacy_patch_path.is_file():
        try:
            legacy = json.loads(legacy_metadata_path.read_text(encoding="utf-8"))
            legacy_run = str(legacy["run_id"])
            legacy_task = int(legacy["task"])
            destination = paths(legacy_run, legacy_task)
            stop_existing_watcher(legacy)
            if not destination["patch"].exists():
                _atomic_write(destination["patch"], legacy_patch_path.read_bytes())
            if legacy_plan_path.is_file() and not destination["plan"].exists():
                _atomic_write(destination["plan"], legacy_plan_path.read_bytes())
            migrated = {
                **legacy,
                "schema_version": SCHEMA_VERSION,
                "run_id": legacy_run,
                "task": legacy_task,
                "watcher_pid": None,
                "updated_at": float(legacy.get("updated_at", time.time())),
                "patch_bytes": destination["patch"].stat().st_size,
                "migrated_from_shared_state": True,
            }
            _atomic_write(destination["metadata"], json.dumps(migrated, ensure_ascii=False, sort_keys=True) + "\n")
            legacy_metadata_path.unlink(missing_ok=True)
            legacy_patch_path.unlink(missing_ok=True)
            legacy_plan_path.unlink(missing_ok=True)
            (directory / "worktree-checkpoint.stop").unlink(missing_ok=True)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise RuntimeError(
                "The legacy shared checkpoint could not be migrated safely; original files were preserved."
            ) from exc
    candidates = []
    for metadata_path in directory.glob("worktree-checkpoint-*.json"):
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            run_id, task = metadata["run_id"], int(metadata["task"])
            state = paths(str(run_id), task)
            if state["metadata"] == metadata_path and state["patch"].is_file():
                candidates.append((float(metadata.get("updated_at", 0)), metadata, state))
        except (OSError, ValueError, KeyError, TypeError):
            continue
    if not candidates:
        return None
    _, metadata, state = max(candidates, key=lambda item: item[0])
    return metadata, state


def restore(base_sha: str) -> bool:
    latest = _latest_checkpoint()
    if latest is None:
        return False
    metadata, state = latest
    stop_existing_watcher(metadata)
    _atomic_write(state["stop"], "stop\n")
    current_head = git("rev-parse", "HEAD").stdout.strip()
    if current_head != base_sha:
        raise RuntimeError("The checked-out HEAD does not match the immutable base SHA; checkpoint was preserved.")
    saved_base = metadata.get("base_sha")
    if saved_base != base_sha:
        print(
            "Checkpoint base differs from current main; attempting a strict context-checked restore. "
            "The checkpoint remains untouched if any hunk cannot be applied cleanly.",
            file=sys.stderr,
        )
    patch = state["patch"].read_text(encoding="utf-8")
    if not patch.strip():
        if state["plan"].is_file():
            (ROOT / ".agent-progress.json").write_bytes(state["plan"].read_bytes())
        return False
    check = subprocess.run(
        ["git", "apply", "--check", "--binary"], input=patch, cwd=ROOT,
        capture_output=True, text=True, encoding="utf-8", errors="replace", check=False,
    )
    if check.returncode:
        raise RuntimeError("The saved worktree checkpoint no longer applies cleanly; it was preserved.")
    apply = subprocess.run(
        ["git", "apply", "--binary"], input=patch, cwd=ROOT,
        capture_output=True, text=True, encoding="utf-8", errors="replace", check=False,
    )
    if apply.returncode:
        raise RuntimeError("Could not restore the saved worktree checkpoint; it was preserved.")
    if state["plan"].is_file():
        (ROOT / ".agent-progress.json").write_bytes(state["plan"].read_bytes())
    return True


def snapshot(base_sha: str, run_id: str, task: int) -> None:
    write_snapshot(base_sha, run_id, task, None)


def stop(run_id: str, task: int) -> None:
    state = paths(run_id, task)
    metadata = read_metadata(run_id, task)
    stop_existing_watcher(metadata)
    _atomic_write(state["stop"], "stop\n")


def clear(run_id: str, task: int) -> None:
    state = paths(run_id, task)
    metadata = read_metadata(run_id, task)
    stop_existing_watcher(metadata)
    _atomic_write(state["stop"], "stop\n")
    for key in ("patch", "metadata", "plan", "stop"):
        state[key].unlink(missing_ok=True)
    ready, error = watcher_marker_paths(run_id, task)
    ready.unlink(missing_ok=True)
    error.unlink(missing_ok=True)



def clear_latest() -> None:
    latest = _latest_checkpoint()
    if latest is None:
        return
    metadata, state = latest
    stop_existing_watcher(metadata)
    _atomic_write(state["stop"], "stop\n")
    for key in ("patch", "metadata", "plan", "stop"):
        state[key].unlink(missing_ok=True)
    ready, error = watcher_marker_paths(str(metadata["run_id"]), int(metadata["task"]))
    ready.unlink(missing_ok=True)
    error.unlink(missing_ok=True)


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
    stop_parser = subs.add_parser("stop")
    stop_parser.add_argument("--run-id", required=True)
    stop_parser.add_argument("--task", required=True, type=int)
    clear_parser = subs.add_parser("clear")
    clear_parser.add_argument("--run-id", required=True)
    clear_parser.add_argument("--task", required=True, type=int)
    subs.add_parser("clear-latest")
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
        stop(args.run_id, args.task)
        return 0
    if args.command == "clear-latest":
        clear_latest()
        return 0
    clear(args.run_id, args.task)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"Autonomous checkpoint failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(1)
