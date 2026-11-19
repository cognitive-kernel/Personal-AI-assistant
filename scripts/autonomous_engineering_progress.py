"""Persist bounded, non-sensitive progress telemetry for the local engineering worker."""
from __future__ import annotations

import argparse
import json
import os
import queue
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from .autonomous_engineering_state_paths import execution_state_directory
except ImportError:  # Direct script execution from the scripts directory.
    from autonomous_engineering_state_paths import execution_state_directory

try:
    from .autonomous_engineering_process_job import WindowsProcessJob
except ImportError:  # Direct script execution from the scripts directory.
    from autonomous_engineering_process_job import WindowsProcessJob

try:
    from .autonomous_engineering_process_group import terminate_process_group
except ImportError:  # Direct script execution from the scripts directory.
    from autonomous_engineering_process_group import terminate_process_group

ROOT = Path(__file__).resolve().parents[1]
PLAN_FILE = ROOT / ".agent-progress.json"
SCORECARD = ROOT / "docs" / "ACCEPTANCE_SCORECARD.md"


try:
    from .autonomous_engineering_codex_config import (
        codex_command,
        _verified_codex_context_window,
        write_codex_model_catalog,
        codex_arguments,
    )
    from .autonomous_engineering_codex_diagnostics import (
        _diagnostic_error_text,
        _diagnostic_field_names,
        _sanitize_error_signature,
        diagnostic_failure_category,
        codex_smoke_evidence,
        write_smoke_evidence,
    )
except ImportError:  # Direct script execution from the scripts directory.
    from autonomous_engineering_codex_config import (
        codex_command,
        _verified_codex_context_window,
        write_codex_model_catalog,
        codex_arguments,
    )
    from autonomous_engineering_codex_diagnostics import (
        _diagnostic_error_text,
        _diagnostic_field_names,
        _sanitize_error_signature,
        diagnostic_failure_category,
        codex_smoke_evidence,
        write_smoke_evidence,
    )


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def store_dir() -> Path:
    return execution_state_directory()


def append_event(record: dict[str, Any]) -> None:
    directory = store_dir()
    directory.mkdir(parents=True, exist_ok=True)
    events = directory / "events.jsonl"
    if events.exists() and events.stat().st_size > 1024 * 1024:
        retained = events.read_text(encoding="utf-8").splitlines()[-1000:]
        temporary = directory / "events.jsonl.tmp"
        temporary.write_text("\n".join(retained) + ("\n" if retained else ""), encoding="utf-8")
        temporary.replace(events)
    with events.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(record, ensure_ascii=False) + "\n")


def append_history(record: dict[str, Any]) -> None:
    directory = store_dir()
    directory.mkdir(parents=True, exist_ok=True)
    history = directory / "history.jsonl"
    if history.exists() and history.stat().st_size > 8 * 1024 * 1024:
        retained = history.read_text(encoding="utf-8").splitlines()[-5000:]
        temporary = directory / "history.jsonl.tmp"
        temporary.write_text("\n".join(retained) + ("\n" if retained else ""), encoding="utf-8")
        temporary.replace(history)
    with history.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(record, ensure_ascii=False) + "\n")


def persist(record: dict[str, Any]) -> bool:
    directory = store_dir()
    try:
        directory.mkdir(parents=True, exist_ok=True)
        payload = {**record, "updated_at": now()}
        temporary = directory / "current.json.tmp"
        temporary.write_text(json.dumps(payload, ensure_ascii=False) + "\n", encoding="utf-8")
        temporary.replace(directory / "current.json")
    except OSError as exc:
        print(f"Progress telemetry unavailable: {type(exc).__name__}: {exc}", file=sys.stderr)
        return False
    try:
        append_event({"type": "progress_event", **payload})
    except OSError as exc:
        print(f"Progress event log unavailable: {type(exc).__name__}: {exc}", file=sys.stderr)
    return True


def record_phase(
    run_id: str, run_attempt: str, phase: str, label: str, activity: str,
    percent: int | None = None, status: str = "running",
) -> bool:
    previous: dict[str, Any] = {}
    try:
        value = json.loads((store_dir() / "current.json").read_text(encoding="utf-8"))
        if isinstance(value, dict) and (
            str(value.get("run_id")) == str(run_id)
            and str(value.get("run_attempt", "1")) == str(run_attempt)
        ):
            previous = value
    except (OSError, ValueError):
        pass
    return persist({
        "run_id": run_id, "run_attempt": run_attempt,
        "status": status, "phase": phase,
        "phase_label": label, "activity": activity,
        "percent": max(0, min(100, percent)) if isinstance(percent, int) else None,
        "plan_completed": previous.get("plan_completed", 0),
        "plan_total": previous.get("plan_total", 0),
        "plan_percent": previous.get("plan_percent"),
    })


def plan_percentage(completed: int, total: int) -> float | None:
    if total <= 0:
        return None
    return round(max(0, min(total, completed)) * 100 / total, 2)


def plan_state() -> tuple[int, int, str | None]:
    try:
        value = json.loads(PLAN_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return 0, 0, None
    tasks = value.get("plan") if isinstance(value, dict) else None
    if not isinstance(tasks, list) or not tasks:
        return 0, 0, None
    valid = [task for task in tasks if isinstance(task, dict)]
    if not valid:
        return 0, 0, None
    completed = sum(task.get("status") in {"completed", "done"} for task in valid)
    active = next((task for task in valid if task.get("status") in {"in_progress", "active"}), None)
    title = active.get("title") if active else None
    if not isinstance(title, str):
        title = None
    return completed, len(valid), title[:180] if title else None


def codex_activity(event: dict[str, Any]) -> str | None:
    event_type = event.get("type")
    if event_type == "turn.started":
        return "عامل در حال تحلیل و اجرای وظیفه است"
    item = event.get("item")
    if not isinstance(item, dict):
        return None
    kind = item.get("type")
    if event_type == "item.started" and kind == "command_execution":
        command = str(item.get("command", "")).strip()
        executable = command.split(maxsplit=1)[0].replace("\\", "/").rsplit("/", 1)[-1] if command else ""
        return f"در حال اجرای ابزار {executable[:48]}" if executable else "در حال اجرای دستور"
    if event_type == "item.completed" and kind in {"file_change", "file_changes"}:
        changes = item.get("changes", [])
        paths = [str(change.get("path", "")) for change in changes if isinstance(change, dict)]
        paths = [path for path in paths if path and len(path) <= 180][:3]
        return "به‌روزرسانی فایل‌ها: " + "، ".join(paths) if paths else "ثبت تغییرات کد"
    if event_type == "item.updated" and kind in {"todo_list", "plan_update"}:
        return "به‌روزرسانی برنامهٔ کار عامل"
    return None



CODEX_PROGRESS_HEARTBEAT_SECONDS = 60


def codex_heartbeat_record(args: argparse.Namespace, elapsed_seconds: float, event_count: int) -> dict[str, Any]:
    """Describe a live Codex process even when Ollama has not emitted JSONL yet."""
    elapsed_minutes = max(0, int(max(0.0, elapsed_seconds) // 60))
    completed, total, active_title = plan_state()
    if active_title:
        activity = active_title
    elif event_count == 0:
        activity = f"در انتظار اولین رویداد از Codex/Ollama؛ نشست هنوز فعال است ({elapsed_minutes} دقیقه)"
    else:
        activity = f"نشست Codex فعال است؛ {event_count} رویداد دریافت شده ({elapsed_minutes} دقیقه)"
    return {
        "run_id": args.run_id, "run_attempt": args.run_attempt,
        "status": "running", "phase": "coding",
        "phase_label": "تحلیل و پیاده‌سازی",
        "activity": activity,
        "plan_completed": completed, "plan_total": total,
        "plan_percent": plan_percentage(completed, total),
    }

def run_codex(args: argparse.Namespace) -> int:
    executable = shutil.which("codex")
    if not executable:
        print("Codex CLI executable was not found on PATH.", file=sys.stderr)
        return 127
    arguments = codex_arguments(args.model, args.report)
    command = codex_command(
        executable, arguments,
        parent_job_managed=os.name == "nt",
        parent_group_managed=os.name != "nt",
    )
    try:
        prompt = Path(args.prompt).read_text(encoding="utf-8")
    except OSError as exc:
        print(f"Cannot read Codex prompt: {exc}", file=sys.stderr)
        return 2
    job = WindowsProcessJob()
    job.__enter__()
    process: subprocess.Popen[str] | None = None
    try:
        process = subprocess.Popen(
            command, cwd=ROOT, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=None, text=True, encoding="utf-8", errors="replace", bufsize=1,
            creationflags=WindowsProcessJob.CREATE_SUSPENDED if os.name == "nt" else 0,
            start_new_session=os.name != "nt",
        )
        if os.name == "nt":
            job.assign_and_resume(process)
    except BaseException:
        try:
            job.terminate_remaining_processes(125)
        finally:
            try:
                if process is not None and process.poll() is None:
                    process.kill()
                    process.wait(timeout=5)
            finally:
                job.close()
        raise
    assert process.stdin is not None and process.stdout is not None
    input_broken = False
    try:
        process.stdin.write(prompt)
    except BrokenPipeError:
        input_broken = True
    finally:
        try:
            process.stdin.close()
        except BrokenPipeError:
            input_broken = True
    if input_broken:
        print(
            "Codex CLI exited before accepting its prompt; inspect the Codex CLI error above.",
            file=sys.stderr,
        )
    last_signature: tuple[Any, ...] | None = None
    smoke_events: list[dict[str, Any]] = []
    if not input_broken:
        persist({
            "run_id": args.run_id, "run_attempt": args.run_attempt, "status": "running",
            "phase": "coding", "phase_label": "تحلیل و پیاده‌سازی",
            "activity": "عامل در حال شروع تحلیل است",
            "plan_completed": 0, "plan_total": 0, "plan_percent": None,
        })
    line_events: queue.Queue[str | None] = queue.Queue()

    def read_codex_output() -> None:
        try:
            for output_line in process.stdout:
                line_events.put(output_line)
        finally:
            line_events.put(None)

    output_reader = threading.Thread(
        target=read_codex_output, name="autonomous-codex-jsonl-reader", daemon=True,
    )
    output_reader.start()
    codex_started_at = time.monotonic()
    next_heartbeat_at = codex_started_at + CODEX_PROGRESS_HEARTBEAT_SECONDS
    event_count = 0
    stream_closed = False
    try:
        while not stream_closed:
            wait_seconds = max(0.1, min(5.0, next_heartbeat_at - time.monotonic()))
            try:
                line = line_events.get(timeout=wait_seconds)
            except queue.Empty:
                if process.poll() is not None and not output_reader.is_alive():
                    break
                now_monotonic = time.monotonic()
                if now_monotonic >= next_heartbeat_at:
                    elapsed = now_monotonic - codex_started_at
                    heartbeat = codex_heartbeat_record(args, elapsed, event_count)
                    persist(heartbeat)
                    print(
                        "[autonomous-progress] " + str(heartbeat["activity"]),
                        flush=True,
                    )
                    next_heartbeat_at = now_monotonic + CODEX_PROGRESS_HEARTBEAT_SECONDS
                continue
            if line is None:
                stream_closed = True
                continue
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if not isinstance(event, dict):
                continue
            event_count += 1
            if getattr(args, "require_command_output", None) or getattr(args, "expected_agent_message", None):
                if (
                    event.get("type") in {"item.started", "item.completed", "turn.completed", "turn.failed", "error"}
                    and (
                        event.get("type") != "item.completed"
                        or (
                            isinstance(event.get("item"), dict)
                            and event["item"].get("type") in {"command_execution", "agent_message", "error"}
                        )
                    )
                ):
                    smoke_events.append(event)
            completed, total, active_title = plan_state()
            activity = active_title or codex_activity(event) or "عامل در حال کار روی وظیفهٔ جاری است"
            signature = (activity, completed, total)
            if signature != last_signature:
                persist({
                    "run_id": args.run_id, "run_attempt": args.run_attempt,
                    "status": "running", "phase": "coding",
                    "phase_label": "تحلیل و پیاده‌سازی",
                    "activity": activity,
                    "plan_completed": completed, "plan_total": total,
                    "plan_percent": plan_percentage(completed, total),
                })
                last_signature = signature
        return_code = process.wait()
        if os.name == "nt":
            residual_processes = job.terminate_remaining_processes(125)
        else:
            process_pid = getattr(process, "pid", None)
            residual_processes = (
                terminate_process_group(process_pid)
                if isinstance(process_pid, int) and process_pid > 0
                else False
            )
        if residual_processes:
            print(
                "Codex supervisor exited while its owned process tree still contained descendants; "
                "the controller terminated and drained them.",
                file=sys.stderr,
            )
            return_code = return_code or 1
        if input_broken and return_code == 0:
            return_code = 1
        expected_output = getattr(args, "require_command_output", None)
        expected_message = getattr(args, "expected_agent_message", None)
        evidence_path = getattr(args, "events_report", None)
        if expected_output or expected_message:
            evidence = codex_smoke_evidence(
                smoke_events,
                expected_output or "",
                expected_message or "",
                getattr(args, "require_command_fragment", None),
            )
            if evidence_path:
                write_smoke_evidence(evidence_path, evidence)
            print(
                "Codex command-execution smoke evidence: "
                + json.dumps(evidence, ensure_ascii=False, sort_keys=True),
                file=sys.stderr,
            )
            if not evidence["passed"]:
                print("Codex JSONL smoke evidence did not satisfy every required check.", file=sys.stderr)
                return_code = return_code or 1
        completed, total, active_title = plan_state()
        persist({
            "run_id": args.run_id, "run_attempt": args.run_attempt,
            "status": "completed" if return_code == 0 else "failed",
            "phase": "coding", "phase_label": "پایان مرحلهٔ کدنویسی",
            "activity": "مرحلهٔ کدنویسی پایان یافت" if return_code == 0 else "مرحلهٔ کدنویسی با خطا متوقف شد",
            "plan_completed": completed, "plan_total": total,
            "plan_percent": plan_percentage(completed, total),
        })
        return return_code
    finally:
        try:
            if process is not None:
                if os.name == "nt":
                    job.terminate_remaining_processes(125)
                    if process.poll() is None:
                        try:
                            process.wait(timeout=1)
                        except subprocess.TimeoutExpired:
                            # If Job Object assignment failed, the suspended
                            # wrapper is outside the job and must be killed directly.
                            process.kill()
                            process.wait(timeout=5)
                else:
                    process_pid = getattr(process, "pid", None)
                    if isinstance(process_pid, int) and process_pid > 0:
                        terminate_process_group(process_pid)
                    if process.poll() is None:
                        try:
                            process.wait(timeout=1)
                        except subprocess.TimeoutExpired:
                            process.kill()
                            process.wait(timeout=5)
        finally:
            try:
                job.close()
            finally:
                try:
                    PLAN_FILE.unlink(missing_ok=True)
                except OSError:
                    pass


def snapshot_project(run_id: str, run_attempt: str) -> bool:
    try:
        content = SCORECARD.read_text(encoding="utf-8")
    except OSError as exc:
        print(f"Could not read acceptance scorecard: {type(exc).__name__}", file=sys.stderr)
        return False
    engineering = re.search(r"Engineering completion:.*?=\s*([0-9]+(?:\.[0-9]+)?)%", content)
    overall = re.search(r"Overall acceptance progress:.*?=\s*([0-9]+(?:\.[0-9]+)?)%", content)
    snapshot = re.search(r"\*\*Snapshot:\*\*\s*(\d{4}-\d{2}-\d{2})", content)
    sha = re.search(r"\*\*Scored baseline:\*\*.*?([0-9a-f]{40})", content)
    if not (engineering and overall and snapshot):
        print("Acceptance scorecard is missing required snapshot or percentage fields.", file=sys.stderr)
        return False
    signature = {
        "scorecard_snapshot_date": snapshot.group(1),
        "engineering_percent": float(engineering.group(1)),
        "overall_percent": float(overall.group(1)),
        "sha": sha.group(1) if sha else None,
    }
    directory = store_dir()
    state_path = directory / "scorecard-state.json"
    previous: dict[str, Any] | None = None
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
        if isinstance(state, dict):
            previous = state
    except (OSError, ValueError):
        pass
    if previous is None:
        history_path = directory / "history.jsonl"
        try:
            if history_path.is_file() and history_path.stat().st_size <= 8 * 1024 * 1024:
                for line in reversed(history_path.read_text(encoding="utf-8").splitlines()):
                    try:
                        candidate = json.loads(line)
                    except ValueError:
                        continue
                    if isinstance(candidate, dict) and candidate.get("type") == "project_snapshot":
                        previous = {
                            key: candidate.get(key)
                            for key in ("scorecard_snapshot_date", "engineering_percent", "overall_percent", "sha")
                        }
                        break
        except OSError:
            pass
    try:
        directory.mkdir(parents=True, exist_ok=True)
        baseline_path = directory / "scorecard-baseline.json"
        if not baseline_path.exists():
            baseline = {
                "date": signature["scorecard_snapshot_date"],
                "engineering_percent": signature["engineering_percent"],
                "overall_percent": signature["overall_percent"],
                "sha": signature["sha"],
                "source": "scorecard-baseline",
            }
            history_path = directory / "history.jsonl"
            if history_path.is_file() and history_path.stat().st_size <= 8 * 1024 * 1024:
                for line in history_path.read_text(encoding="utf-8").splitlines():
                    try:
                        candidate = json.loads(line)
                    except ValueError:
                        continue
                    if isinstance(candidate, dict) and candidate.get("type") == "project_snapshot":
                        baseline = {
                            "date": candidate.get("scorecard_snapshot_date") or candidate.get("date"),
                            "engineering_percent": candidate.get("engineering_percent"),
                            "overall_percent": candidate.get("overall_percent"),
                            "sha": candidate.get("sha"),
                            "source": "scorecard-baseline",
                        }
                        break
            baseline_temp = baseline_path.with_suffix(".json.tmp")
            baseline_temp.write_text(json.dumps(baseline, ensure_ascii=False) + "\n", encoding="utf-8")
            baseline_temp.replace(baseline_path)
        same_scores = previous is not None and (
            previous.get("engineering_percent") == signature["engineering_percent"]
            and previous.get("overall_percent") == signature["overall_percent"]
        )
        if previous is not None and not same_scores:
            record = {
                "type": "project_snapshot", "date": datetime.now(timezone.utc).date().isoformat(),
                **signature, "recorded_at": now(), "run_id": run_id, "run_attempt": run_attempt,
            }
            append_history(record)
        temporary = state_path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(signature, ensure_ascii=False) + "\n", encoding="utf-8")
        temporary.replace(state_path)
    except OSError as exc:
        print(f"Could not persist scorecard snapshot: {type(exc).__name__}", file=sys.stderr)
        return False
    return True


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="action", required=True)
    record = sub.add_parser("record")
    record.add_argument("--run-id", required=True)
    record.add_argument("--run-attempt", default="1")
    record.add_argument("--phase", required=True)
    record.add_argument("--label", required=True)
    record.add_argument("--activity", required=True)
    record.add_argument("--status", default="running")
    codex = sub.add_parser("codex")
    codex.add_argument("--run-id", required=True)
    codex.add_argument("--run-attempt", default="1")
    codex.add_argument("--model", required=True)
    codex.add_argument("--prompt", required=True)
    codex.add_argument("--report", required=True)
    codex.add_argument("--require-command-output")
    codex.add_argument("--expected-agent-message")
    codex.add_argument("--events-report")
    codex.add_argument("--require-command-fragment", action="append", default=[])
    snapshot = sub.add_parser("snapshot")
    snapshot.add_argument("--run-id", required=True)
    snapshot.add_argument("--run-attempt", default="1")
    args = parser.parse_args()
    if args.action == "record":
        return 0 if record_phase(
            args.run_id, args.run_attempt, args.phase, args.label,
            args.activity, status=args.status,
        ) else 1
    if args.action == "codex":
        return run_codex(args)
    return 0 if snapshot_project(args.run_id, args.run_attempt) else 1


if __name__ == "__main__":
    raise SystemExit(main())
