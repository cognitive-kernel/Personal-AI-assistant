"""Persist bounded, non-sensitive progress telemetry for the local engineering worker."""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
PLAN_FILE = ROOT / ".agent-progress.json"
SCORECARD = ROOT / "docs" / "ACCEPTANCE_SCORECARD.md"


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def store_dir() -> Path:
    configured = os.getenv("AUTONOMOUS_ENGINEERING_PROGRESS_DIR", "").strip()
    if configured:
        return Path(configured).expanduser()
    program_data = os.getenv("PROGRAMDATA")
    if program_data:
        return Path(program_data) / "PersonalAIAssistant" / "autonomous-engineering"
    return Path.home() / ".personal-ai-assistant" / "autonomous-engineering"



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


def codex_command(executable: str, arguments: list[str]) -> list[str]:
    if Path(executable).suffix.casefold() in {".cmd", ".bat"}:
        return [
            os.getenv("COMSPEC", "cmd.exe"), "/d", "/c", "call",
            executable, *arguments,
        ]
    return [executable, *arguments]


def codex_arguments(model: str, report: str) -> list[str]:
    # Pin the provider and the Windows sandbox backend explicitly. The native
    # unelevated backend in affected Codex CLI releases can reject exec_command
    # before process creation or refuse workspace-write root layouts. The
    # workflow selects a backend only after a real in-workspace tool smoke test.
    backend = os.getenv("CODEX_WINDOWS_SANDBOX_BACKEND", "mxc").strip().casefold()
    if backend not in {"mxc", "elevated"}:
        raise ValueError("CODEX_WINDOWS_SANDBOX_BACKEND must be 'mxc' or 'elevated'.")
    reasoning_effort = os.getenv("CODEX_MODEL_REASONING_EFFORT", "").strip().casefold()
    if reasoning_effort and reasoning_effort not in {"low", "medium", "high"}:
        raise ValueError("CODEX_MODEL_REASONING_EFFORT must be 'low', 'medium', or 'high'.")
    arguments = [
        "exec", "-c", 'model_provider="ollama"',
        "-c", f'windows.sandbox="{backend}"',
        "--oss", "--local-provider", "ollama",
        "--sandbox", "workspace-write",
        "--model", model, "--cd", str(ROOT),
        "--output-last-message", report, "--json", "-",
    ]
    # On Windows, the legacy workspace-write policy can project the Unix
    # /tmp special root onto a drive-root path and create an unsupported split
    # writable-root set for exec_command. Exclude that unintended root while
    # preserving the workspace and the normal per-user temp directory.
    arguments[5:5] = ["-c", "sandbox_workspace_write.exclude_slash_tmp=true"]
    if reasoning_effort:
        arguments[5:5] = ["-c", f'model_reasoning_effort="{reasoning_effort}"']
    return arguments


def _diagnostic_error_text(value: Any, depth: int = 0) -> str:
    """Read only structured error fields for classification; never persist their values."""
    if depth > 3:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        allowed = {"item", "error", "message", "reason", "detail", "stderr", "stdout", "aggregated_output", "code", "kind"}
        return " ".join(
            _diagnostic_error_text(child, depth + 1)
            for key, child in value.items()
            if str(key).casefold() in allowed
        )
    if isinstance(value, list):
        return " ".join(_diagnostic_error_text(child, depth + 1) for child in value[:8])
    return ""


def _diagnostic_field_names(value: Any, depth: int = 0) -> list[str]:
    if depth > 3 or not isinstance(value, dict):
        return []
    names = set(value)
    for key in ("item", "error", "details", "cause"):
        names.update(_diagnostic_field_names(value.get(key), depth + 1))
    return sorted(str(name) for name in names)


def diagnostic_failure_category(message: Any) -> str:
    """Classify an error without retaining command text or process output."""
    text = _diagnostic_error_text(message).casefold()
    if not text.strip():
        return "no_error_detail"
    if "blocked by policy" in text or ("createprocess" in text and "rejected" in text):
        return "process_launch_rejected"
    if any(token in text for token in ("unsupportedoperation", "restricted-token", "split writable root")):
        return "unsupported_sandbox_policy"
    if any(token in text for token in ("access is denied", "access denied", "permission denied", "unauthorized")):
        return "permission_denied"
    if "sandbox" in text and any(token in text for token in ("denied", "blocked", "violation", "permission")):
        return "sandbox_denied"
    if any(token in text for token in ("timed out", "timeout", "deadline exceeded")):
        return "timeout"
    if any(token in text for token in ("not recognized as an internal or external command", "command not found", "executable not found")):
        return "executable_not_found"
    if any(token in text for token in ("cannot find the path", "path not found", "no such file or directory")):
        return "path_not_found"
    if any(token in text for token in ("invalid argument", "unknown argument", "unexpected argument", "invalid tool arguments")):
        return "invalid_tool_arguments"
    if any(token in text for token in ("failed to spawn", "failed to start process", "could not start process")):
        return "process_start_failed"
    return "unclassified_error"


def codex_smoke_evidence(
    events: list[dict[str, Any]],
    expected_command_output: str,
    expected_agent_message: str,
    required_command_fragments: list[str] | None = None,
) -> dict[str, Any]:
    started_commands = [
        event["item"]
        for event in events
        if event.get("type") == "item.started"
        and isinstance(event.get("item"), dict)
        and event["item"].get("type") == "command_execution"
    ]
    completed_commands = [
        event["item"]
        for event in events
        if event.get("type") == "item.completed"
        and isinstance(event.get("item"), dict)
        and event["item"].get("type") == "command_execution"
    ]
    agent_messages = [
        str(event["item"].get("text", "")).strip()
        for event in events
        if event.get("type") == "item.completed"
        and isinstance(event.get("item"), dict)
        and event["item"].get("type") == "agent_message"
    ]
    turn_completed = any(event.get("type") == "turn.completed" for event in events)
    error_events = [
        event for event in events
        if event.get("type") in {"turn.failed", "error"}
        or (isinstance(event.get("item"), dict) and event["item"].get("type") == "error")
    ]
    turn_failed = bool(error_events)
    error_summaries = [
        {
            "category": diagnostic_failure_category(event),
            "field_names": _diagnostic_field_names(event),
        }
        for event in error_events
    ]
    error_categories = [summary["category"] for summary in error_summaries]
    required_fragments = required_command_fragments or []

    def index_by_id(items: list[dict[str, Any]]) -> tuple[dict[str, dict[str, Any]], bool]:
        indexed: dict[str, dict[str, Any]] = {}
        duplicate_or_missing_id = False
        for item in items:
            item_id = str(item.get("id", "")).strip()
            if not item_id or item_id in indexed:
                duplicate_or_missing_id = True
                continue
            indexed[item_id] = item
        return indexed, duplicate_or_missing_id

    started_by_id, duplicate_start_ids = index_by_id(started_commands)
    completed_by_id, duplicate_completion_ids = index_by_id(completed_commands)
    ids_match = (
        not duplicate_start_ids
        and not duplicate_completion_ids
        and bool(started_by_id)
        and set(started_by_id) == set(completed_by_id)
        and len(started_commands) == len(started_by_id)
        and len(completed_commands) == len(completed_by_id)
    )

    command_summaries: list[dict[str, Any]] = []
    verified_probes: list[dict[str, Any]] = []
    all_commands_succeeded = bool(completed_commands)
    all_command_payloads_match = ids_match

    for completed in completed_commands:
        item_id = str(completed.get("id", "")).strip()
        started = started_by_id.get(item_id, {})
        started_text = str(started.get("command", "")).strip()
        completed_text = str(completed.get("command", "")).strip()
        command_text = completed_text or started_text
        command_payload_matches = bool(command_text) and (
            not started_text or not completed_text or started_text == completed_text
        )
        output_text = str(completed.get("aggregated_output", ""))
        fragment_checks = {
            fragment: fragment in command_text
            for fragment in required_fragments
        }
        fragments_present = all(fragment_checks.values())
        output_marker_present = bool(expected_command_output) and expected_command_output in output_text
        succeeded = completed.get("status") == "completed" and completed.get("exit_code") == 0
        all_commands_succeeded = all_commands_succeeded and succeeded
        all_command_payloads_match = all_command_payloads_match and command_payload_matches
        summary = {
            "id": item_id,
            "status": completed.get("status"),
            "exit_code": completed.get("exit_code"),
            "start_completed_match": command_payload_matches,
            "required_fragments": fragment_checks,
            "output_marker_present": output_marker_present,
            "failure_category": None if succeeded else diagnostic_failure_category(completed),
        }
        command_summaries.append(summary)
        if succeeded and command_payload_matches and fragments_present and output_marker_present:
            verified_probes.append(completed)

    final_message = agent_messages[-1] if agent_messages else ""
    exactly_one_verified_probe = len(verified_probes) == 1
    proof_command = verified_probes[0] if exactly_one_verified_probe else {}
    checks = {
        "command_events_are_paired": ids_match,
        "command_payloads_match": all_command_payloads_match,
        "all_commands_completed_successfully": all_commands_succeeded,
        "exactly_one_verified_workspace_probe": exactly_one_verified_probe,
        "turn_completed": turn_completed,
        "turn_not_failed": not turn_failed,
        "expected_agent_message": not expected_agent_message or final_message == expected_agent_message,
    }

    completed_turns = [event for event in events if event.get("type") == "turn.completed"]
    usage = completed_turns[-1].get("usage", {}) if completed_turns else {}
    event_types = [str(event.get("type", "")) for event in events]
    item_types = [
        str(event["item"].get("type", ""))
        for event in events
        if isinstance(event.get("item"), dict)
    ]
    turn_usage = {
        key: usage[key]
        for key in ("input_tokens", "output_tokens", "total_tokens")
        if isinstance(usage, dict) and key in usage
    }

    # Evidence deliberately contains no raw command, prompt, output, or agent text.
    return {
        "passed": all(checks.values()),
        "checks": checks,
        "command_status": proof_command.get("status"),
        "command_exit_code": proof_command.get("exit_code"),
        "command_output_marker": exactly_one_verified_probe,
        "required_command_fragments_present": exactly_one_verified_probe,
        "agent_message_matches_expected": not expected_agent_message or final_message == expected_agent_message,
        "turn_completed": turn_completed,
        "turn_failed": turn_failed,
        "error_event_count": len(error_events),
        "error_categories": error_categories,
        "error_summaries": error_summaries,
        "event_count": len(events),
        "event_types": event_types,
        "item_types": item_types,
        "command_started_count": len(started_commands),
        "command_completed_count": len(completed_commands),
        "agent_message_count": len(agent_messages),
        "turn_usage": turn_usage,
        "command_summaries": command_summaries,
    }

def write_smoke_evidence(path: str, evidence: dict[str, Any]) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    temporary.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(destination)


def run_codex(args: argparse.Namespace) -> int:
    executable = shutil.which("codex")
    if not executable:
        print("Codex CLI executable was not found on PATH.", file=sys.stderr)
        return 127
    arguments = codex_arguments(args.model, args.report)
    command = codex_command(executable, arguments)
    try:
        prompt = Path(args.prompt).read_text(encoding="utf-8")
    except OSError as exc:
        print(f"Cannot read Codex prompt: {exc}", file=sys.stderr)
        return 2
    process = subprocess.Popen(
        command, cwd=ROOT, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=None, text=True, encoding="utf-8", errors="replace", bufsize=1,
    )
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
    try:
        for line in process.stdout:
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if not isinstance(event, dict):
                continue
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
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=10)
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
