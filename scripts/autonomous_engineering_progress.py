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


def persist(record: dict[str, Any]) -> None:
    directory = store_dir()
    try:
        directory.mkdir(parents=True, exist_ok=True)
        payload = {**record, "updated_at": now()}
        temporary = directory / "current.json.tmp"
        temporary.write_text(json.dumps(payload, ensure_ascii=False) + "\n", encoding="utf-8")
        temporary.replace(directory / "current.json")
        append_event({"type": "progress_event", **payload})
    except OSError as exc:
        print(f"Progress telemetry unavailable: {type(exc).__name__}: {exc}", file=sys.stderr)


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


def run_codex(args: argparse.Namespace) -> int:
    executable = shutil.which("codex")
    if not executable:
        print("Codex CLI executable was not found on PATH.", file=sys.stderr)
        return 127
    arguments = [
        "exec", "--oss", "--sandbox", "workspace-write",
        "--model", args.model, "--cd", str(ROOT),
        "--output-last-message", args.report, "--json", "-",
    ]
    if Path(executable).suffix.casefold() in {".cmd", ".bat"}:
        command = [
            os.getenv("COMSPEC", "cmd.exe"), "/d", "/s", "/c",
            '""' + executable + '" ' + subprocess.list2cmdline(arguments) + '"',
        ]
    else:
        command = [executable, *arguments]
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
    process.stdin.write(prompt)
    process.stdin.close()
    last_signature: tuple[Any, ...] | None = None
    persist({
        "run_id": args.run_id, "run_attempt": args.run_attempt, "status": "running",
        "phase": "coding", "phase_label": "تحلیل و پیاده‌سازی",
        "activity": "عامل در حال شروع تحلیل است", "percent": 20,
        "plan_completed": 0, "plan_total": 0,
    })
    try:
        for line in process.stdout:
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if not isinstance(event, dict):
                continue
            completed, total, active_title = plan_state()
            activity = active_title or codex_activity(event) or "عامل در حال کار روی وظیفهٔ جاری است"
            percent = 20 + (45 * completed // total if total else 0)
            signature = (percent, activity, completed, total)
            if signature != last_signature:
                persist({
                    "run_id": args.run_id, "run_attempt": args.run_attempt,
                    "status": "running", "phase": "coding",
                    "phase_label": "تحلیل و پیاده‌سازی",
                    "activity": activity, "percent": percent,
                    "plan_completed": completed, "plan_total": total,
                })
                last_signature = signature
        return_code = process.wait()
        completed, total, active_title = plan_state()
        persist({
            "run_id": args.run_id, "run_attempt": args.run_attempt,
            "status": "completed" if return_code == 0 else "failed",
            "phase": "coding", "phase_label": "پایان مرحلهٔ کدنویسی",
            "activity": "مرحلهٔ کدنویسی پایان یافت" if return_code == 0 else "مرحلهٔ کدنویسی با خطا متوقف شد",
            "percent": 65 if return_code == 0 else 20 + (45 * completed // total if total else 0),
            "plan_completed": completed, "plan_total": total,
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


def snapshot_project(run_id: str, run_attempt: str) -> None:
    try:
        content = SCORECARD.read_text(encoding="utf-8")
    except OSError:
        return
    engineering = re.search(r"Engineering completion:.*?=\s*([0-9]+(?:\.[0-9]+)?)%", content)
    overall = re.search(r"Overall acceptance progress:.*?=\s*([0-9]+(?:\.[0-9]+)?)%", content)
    snapshot = re.search(r"\*\*Snapshot:\*\*\s*(\d{4}-\d{2}-\d{2})", content)
    sha = re.search(r"\*\*Scored baseline:\*\*.*?([0-9a-f]{40})", content)
    if not (engineering and overall and snapshot):
        return
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


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="action", required=True)
    record = sub.add_parser("record")
    record.add_argument("--run-id", required=True)
    record.add_argument("--run-attempt", default="1")
    record.add_argument("--phase", required=True)
    record.add_argument("--label", required=True)
    record.add_argument("--activity", required=True)
    record.add_argument("--percent", type=int, required=True)
    record.add_argument("--status", default="running")
    codex = sub.add_parser("codex")
    codex.add_argument("--run-id", required=True)
    codex.add_argument("--run-attempt", default="1")
    codex.add_argument("--model", required=True)
    codex.add_argument("--prompt", required=True)
    codex.add_argument("--report", required=True)
    snapshot = sub.add_parser("snapshot")
    snapshot.add_argument("--run-id", required=True)
    snapshot.add_argument("--run-attempt", default="1")
    args = parser.parse_args()
    if args.action == "record":
        persist({
            "run_id": args.run_id, "run_attempt": args.run_attempt,
            "status": args.status, "phase": args.phase,
            "phase_label": args.label, "activity": args.activity,
            "percent": max(0, min(100, args.percent)),
        })
        return 0
    if args.action == "codex":
        return run_codex(args)
    snapshot_project(args.run_id, args.run_attempt)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
