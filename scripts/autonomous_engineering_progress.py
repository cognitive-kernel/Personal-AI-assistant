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
    return [
        "exec", "-c", 'model_provider="ollama"',
        "-c", f'windows.sandbox="{backend}"',
        "--oss", "--local-provider", "ollama",
        "--sandbox", "workspace-write",
        "--model", model, "--cd", str(ROOT),
        "--output-last-message", report, "--json", "-",
    ]


def codex_smoke_evidence(
    events: list[dict[str, Any]],
    expected_command_output: str,
    expected_agent_message: str,
) -> dict[str, Any]:
    completed_commands = [
        event["item"]
        for event in events
        if event.get("type") == "item.completed"
        and isinstance(event.get("item"), dict)
        and event["item"].get("type") == "command_execution"
    ]
    agent_messages = [
        event["item"].get("text", "")
        for event in events
        if event.get("type") == "item.completed"
        and isinstance(event.get("item"), dict)
        and event["item"].get("type") == "agent_message"
    ]
    turn_completed = any(event.get("type") == "turn.completed" for event in events)
    turn_failed = any(event.get("type") in {"turn.failed", "error"} for event in events)

    command = completed_commands[0] if len(completed_commands) == 1 else {}
    command_output = str(command.get("aggregated_output", ""))
    final_message = agent_messages[-1].strip() if agent_messages else ""
    checks = {
        "exactly_one_command_execution": len(completed_commands) == 1,
        "command_completed": command.get("status") == "completed",
        "command_exit_code_zero": command.get("exit_code") == 0,
        "command_output_marker_present": expected_command_output in command_output,
        "turn_completed": turn_completed,
        "turn_not_failed": not turn_failed,
        "expected_agent_message": final_message == expected_agent_message,
    }
    return {
        "passed": all(checks.values()),
        "checks": checks,
        "command": str(command.get("command", "")),
        "command_status": command.get("status"),
        "command_exit_code": command.get("exit_code"),
        "command_output_marker": expected_command_output in command_output,
        "agent_message": final_message,
        "turn_completed": turn_completed,
        "turn_failed": turn_failed,
    }


def write_smoke_evidence(path: str, evidence: dict[str, Any]) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    temporary.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\\n", encoding="utf-8")
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
                    event.get("type") in {"item.completed", "turn.completed", "turn.failed", "error"}
                    and (
                        event.get("type") != "item.completed"
                        or (
                            isinstance(event.get("item"), dict)
                            and event["item"].get("type") in {"command_execution", "agent_message"}
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
