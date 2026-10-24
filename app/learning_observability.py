"""Read-only operational metrics and conservative release thresholds for learning.

Metrics are derived from durable SQLite records. Small samples are reported as
insufficient evidence, never silently treated as a healthy zero-failure rate.
"""
from __future__ import annotations

import sqlite3
from typing import Any

DEFAULT_MIN_TERMINAL_SAMPLE = 10
DEFAULT_MAX_FAILURE_RATE = 0.25
DEFAULT_MAX_DAILY_BUDGET_UTILIZATION = 0.90


def collect_learning_operational_metrics(
    connection: sqlite3.Connection,
    *,
    min_terminal_sample: int = DEFAULT_MIN_TERMINAL_SAMPLE,
    max_failure_rate: float = DEFAULT_MAX_FAILURE_RATE,
    max_daily_budget_utilization: float = DEFAULT_MAX_DAILY_BUDGET_UTILIZATION,
) -> dict[str, Any]:
    if isinstance(min_terminal_sample, bool) or not isinstance(min_terminal_sample, int) or min_terminal_sample < 1:
        raise ValueError("min_terminal_sample must be a positive integer.")
    if not 0 < max_failure_rate <= 1:
        raise ValueError("max_failure_rate must be in (0, 1].")
    if not 0 < max_daily_budget_utilization <= 1:
        raise ValueError("max_daily_budget_utilization must be in (0, 1].")

    job_rows = connection.execute(
        """SELECT status, COUNT(*) AS count FROM learning_jobs
           WHERE status IN ('completed', 'failed')
             AND updated_at >= datetime('now', '-7 days')
           GROUP BY status"""
    ).fetchall()
    job_counts = {str(row["status"]): int(row["count"]) for row in job_rows}
    terminal_jobs = sum(job_counts.values())
    failed_jobs = job_counts.get("failed", 0)
    job_failure_rate = failed_jobs / terminal_jobs if terminal_jobs else None

    channel_rows = connection.execute(
        """SELECT status, COUNT(*) AS count FROM learning_candidate_channel_runs
           WHERE status IN ('succeeded', 'failed', 'skipped')
             AND updated_at >= datetime('now', '-7 days')
           GROUP BY status"""
    ).fetchall()
    channel_counts = {str(row["status"]): int(row["count"]) for row in channel_rows}
    terminal_channels = sum(channel_counts.values())
    failed_channels = channel_counts.get("failed", 0)
    channel_failure_rate = failed_channels / terminal_channels if terminal_channels else None

    day_row = connection.execute("SELECT date('now') AS day").fetchone()
    day = str(day_row["day"])
    budget = connection.execute(
        """SELECT max_calls, max_predict_tokens FROM learning_global_daily_budgets
           WHERE budget_date=?""", (day,),
    ).fetchone()
    budget_metrics: dict[str, Any] = {
        "date_utc": day, "configured": budget is not None,
        "calls_reserved": 0, "calls_limit": None,
        "predict_tokens_reserved": 0, "predict_tokens_limit": None,
        "max_utilization": None,
    }
    if budget is not None:
        totals = connection.execute(
            """SELECT COALESCE(SUM(calls_reserved), 0) AS calls_reserved,
                      COALESCE(SUM(predict_tokens_reserved), 0) AS tokens_reserved
               FROM learning_global_budget_reservations WHERE budget_date=?""", (day,),
        ).fetchone()
        calls_limit = int(budget["max_calls"])
        tokens_limit = int(budget["max_predict_tokens"])
        calls_reserved = int(totals["calls_reserved"])
        tokens_reserved = int(totals["tokens_reserved"])
        utilizations = [
            calls_reserved / calls_limit if calls_limit else 1.0,
            tokens_reserved / tokens_limit if tokens_limit else 1.0,
        ]
        budget_metrics.update({
            "calls_reserved": calls_reserved, "calls_limit": calls_limit,
            "predict_tokens_reserved": tokens_reserved, "predict_tokens_limit": tokens_limit,
            "max_utilization": max(utilizations),
        })

    alerts: list[dict[str, Any]] = []
    evaluations: list[str] = []
    for name, sample, rate in (
        ("learning_job_failure_rate", terminal_jobs, job_failure_rate),
        ("review_channel_failure_rate", terminal_channels, channel_failure_rate),
    ):
        if sample < min_terminal_sample:
            evaluations.append(name + ":insufficient_sample")
        else:
            evaluations.append(name + ":evaluated")
            if rate is not None and rate > max_failure_rate:
                alerts.append({
                    "code": name, "severity": "warning", "sample_size": sample,
                    "observed": round(rate, 6), "threshold": max_failure_rate,
                })
    utilization = budget_metrics["max_utilization"]
    if utilization is not None and utilization > max_daily_budget_utilization:
        alerts.append({
            "code": "daily_budget_utilization", "severity": "warning",
            "observed": round(utilization, 6), "threshold": max_daily_budget_utilization,
        })

    if alerts:
        release_gate = "blocked"
    elif all(item.endswith(":evaluated") for item in evaluations):
        release_gate = "pass"
    else:
        release_gate = "insufficient_data"

    return {
        "window": "rolling_7_days_utc",
        "thresholds": {
            "min_terminal_sample": min_terminal_sample,
            "max_failure_rate": max_failure_rate,
            "max_daily_budget_utilization": max_daily_budget_utilization,
        },
        "learning_jobs": {
            "terminal_count": terminal_jobs, "failed_count": failed_jobs,
            "failure_rate": round(job_failure_rate, 6) if job_failure_rate is not None else None,
        },
        "review_channels": {
            "terminal_count": terminal_channels, "failed_count": failed_channels,
            "failure_rate": round(channel_failure_rate, 6) if channel_failure_rate is not None else None,
            "counts_by_status": channel_counts,
        },
        "daily_budget": budget_metrics,
        "evaluations": evaluations,
        "alerts": alerts,
        "release_gate": release_gate,
        "note": "Operational thresholds are guardrails, not proof of scientific correctness or live-model security.",
    }
