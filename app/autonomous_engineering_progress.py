"""Read-only progress telemetry for the local autonomous engineering dashboard."""
from __future__ import annotations

import json
import os
import re
import tempfile

from .autonomous_engineering_state import durable_state_lock, execution_state_directory
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

_DEFAULT_SCORECARD = Path(__file__).resolve().parents[1] / "docs" / "ACCEPTANCE_SCORECARD.md"
_MAX_HISTORY_BYTES = 8 * 1024 * 1024
_MAX_EVENTS = 20000


def _progress_dir() -> Path:
    """Use the same machine-aware state-root contract as the Windows Runner."""
    return execution_state_directory()


def storage_accessible() -> bool:
    """Check the broker boundary, not the dashboard account's filesystem ACL."""
    from .autonomous_engineering_state_broker import health
    return health()


def _shared_state_snapshot() -> dict[str, Any]:
    from .autonomous_engineering_state_broker import progress
    return progress()


def read_run_diagnostics(run_id: int) -> dict[str, Any] | None:
    if isinstance(run_id, bool) or not isinstance(run_id, int) or run_id <= 0:
        return None
    try:
        from .autonomous_engineering_state_broker import diagnostics
        return diagnostics(run_id)
    except (OSError, RuntimeError, ValueError, TypeError):
        return None


def cleanup_shared_progress_data() -> dict[str, Any]:
    from .autonomous_engineering_state_broker import cleanup
    return cleanup()


def _safe_state_broker_error_detail(exc: Exception) -> str:
    """Expose bounded broker diagnostics without leaking arbitrary exception data."""
    try:
        from .autonomous_engineering_state_broker import StateBrokerError
    except ImportError:
        return type(exc).__name__
    if not isinstance(exc, StateBrokerError):
        return type(exc).__name__
    detail = " ".join(str(exc).split())
    return detail[:240] if detail else type(exc).__name__


def pending_work_status() -> dict[str, Any]:
    """Inspect durable ownership markers without reading or changing task data."""
    state_files = {
        "pending_task": "pending-manual-task.txt",
        "pending_owner": "pending-manual-task-owner.json",
        "pending_pr": "pending-autonomous-pr.json",
        "cleanup_in_progress": "pending-state-cleanup.json",
    }
    try:
        from .autonomous_engineering_state_broker import pending_work
        return pending_work()
    except (OSError, RuntimeError, TimeoutError, ValueError) as exc:
        return {
            "available": False,
            "blocked": True,
            "markers": {},
            "reason": f"Durable task state cannot be verified safely: {_safe_state_broker_error_detail(exc)}",
        }


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        if not path.is_file() or path.stat().st_size > 256_000:
            return None
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else None
    except (OSError, ValueError):
        return None


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _scorecard_baseline(saved: dict[str, Any] | None = None) -> dict[str, Any] | None:
    if isinstance(saved, dict):
        saved_date = _parse_date(str(saved.get("date", "")))
        engineering_value = saved.get("engineering_percent")
        overall_value = saved.get("overall_percent")
        if (
            saved_date is not None
            and isinstance(engineering_value, (int, float))
            and isinstance(overall_value, (int, float))
            and 0 <= engineering_value <= 100
            and 0 <= overall_value <= 100
        ):
            return {
                "date": saved_date.isoformat(),
                "engineering_percent": float(engineering_value),
                "overall_percent": float(overall_value),
                "sha": saved.get("sha"),
                "source": "scorecard-baseline",
            }
    try:
        content = _DEFAULT_SCORECARD.read_text(encoding="utf-8")
    except OSError:
        return None
    engineering = re.search(r"Engineering completion:.*?=\s*([0-9]+(?:\.[0-9]+)?)%", content)
    overall = re.search(r"Overall acceptance progress:.*?=\s*([0-9]+(?:\.[0-9]+)?)%", content)
    snapshot = re.search(r"\*\*Snapshot:\*\*\s*(\d{4}-\d{2}-\d{2})", content)
    sha = re.search(r"\*\*Scored baseline:\*\*.*?([0-9a-f]{40})", content)
    if not engineering or not overall or not snapshot:
        return None
    return {
        "date": snapshot.group(1),
        "engineering_percent": float(engineering.group(1)),
        "overall_percent": float(overall.group(1)),
        "sha": sha.group(1) if sha else None,
        "source": "scorecard-baseline",
    }


def _read_history(directory: Path | None = None) -> list[dict[str, Any]]:
    path = (directory or _progress_dir()) / "history.jsonl"
    try:
        if not path.is_file() or path.stat().st_size > _MAX_HISTORY_BYTES:
            return []
        lines = path.read_text(encoding="utf-8").splitlines()[-_MAX_EVENTS:]
    except OSError:
        return []
    records: list[dict[str, Any]] = []
    for line in lines:
        try:
            item = json.loads(line)
        except ValueError:
            continue
        if not isinstance(item, dict):
            continue
        if item.get("type") == "project_snapshot" and isinstance(item.get("date"), str):
            records.append(item)
    return records



def _read_recent_events(directory: Path | None = None) -> list[dict[str, Any]]:
    path = (directory or _progress_dir()) / "events.jsonl"
    try:
        if not path.is_file() or path.stat().st_size > 1024 * 1024:
            return []
        lines = path.read_text(encoding="utf-8").splitlines()[-100:]
    except OSError:
        return []
    events: list[dict[str, Any]] = []
    for line in lines:
        try:
            item = json.loads(line)
        except ValueError:
            continue
        if isinstance(item, dict) and item.get("type") == "progress_event":
            events.append({
                "updated_at": item.get("updated_at"),
                "phase_label": item.get("phase_label"),
                "activity": item.get("activity"),
                "percent": item.get("percent"),
                "status": item.get("status"),
            })
    return events[-50:]


def _aggregate_snapshots(
    records: list[dict[str, Any]], period: str, start: date | None, end: date | None,
    baseline: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    candidates = list(records)
    if baseline:
        candidates.insert(0, baseline)
    selected: dict[str, dict[str, Any]] = {}
    for item in candidates:
        item_date = _parse_date(str(item.get("date", "")))
        if item_date is None or (start and item_date < start) or (end and item_date > end):
            continue
        key = item_date.isoformat() if period == "daily" else f"{item_date.isocalendar().year}-W{item_date.isocalendar().week:02d}"
        previous = selected.get(key)
        if previous is None or str(item.get("recorded_at", "")) >= str(previous.get("recorded_at", "")):
            selected[key] = {
                "period": key,
                "date": item_date.isoformat(),
                "engineering_percent": item.get("engineering_percent"),
                "overall_percent": item.get("overall_percent"),
                "sha": item.get("sha"),
            }
    return [selected[key] for key in sorted(selected)]


def get_progress(
    period: str = "daily", start_date: str | None = None, end_date: str | None = None
) -> dict[str, Any]:
    if period not in {"daily", "weekly", "custom"}:
        period = "daily"
    start = _parse_date(start_date)
    end = _parse_date(end_date)
    if period == "custom" and (start is None or end is None or start > end):
        return {"current": None, "history": [], "available": False, "error": "بازهٔ تاریخ معتبر نیست."}
    try:
        snapshot = _shared_state_snapshot()
    except (OSError, RuntimeError, TimeoutError, ValueError) as exc:
        return {
            "current": None,
            "history": [],
            "latest_snapshot": None,
            "events": [],
            "available": False,
            "storage_configured": False,
            "error": f"Shared progress storage is unavailable: {_safe_state_broker_error_detail(exc)}",
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
    current = snapshot.get("current")
    records = snapshot.get("history") if isinstance(snapshot.get("history"), list) else []
    baseline = _scorecard_baseline(snapshot.get("baseline") if isinstance(snapshot.get("baseline"), dict) else None)
    all_snapshots = _aggregate_snapshots(records, "daily", None, None, baseline)
    latest_snapshot = all_snapshots[-1] if all_snapshots else baseline
    chart_period = "weekly" if period == "weekly" else "daily"
    history = _aggregate_snapshots(records, chart_period, start, end, baseline)
    if period == "daily" and start is None and end is None:
        today = datetime.now(timezone.utc).date()
        start = date.fromordinal(today.toordinal() - 6)
        history = _aggregate_snapshots(records, "daily", start, today, baseline)
    elif period == "weekly" and start is None and end is None:
        today = datetime.now(timezone.utc).date()
        start = date.fromordinal(today.toordinal() - 55)
        history = _aggregate_snapshots(records, "weekly", start, today, baseline)
    return {
        "current": current,
        "history": history,
        "latest_snapshot": latest_snapshot,
        "events": (
            snapshot.get("events", [])[-50:]
            if isinstance(snapshot.get("events"), list)
            else []
        ),
        "available": bool(current or history),
        "storage_configured": bool(snapshot.get("storage_configured")),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
