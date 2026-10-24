"""Durable idempotent per-job and daily-global budgets for learning work.

The per-job budget is shared by generation and review channels. A second durable
ledger caps aggregate reserved requests/output tokens across all jobs per UTC day.
These are request/token ceilings, not provider billing estimates.
"""
from __future__ import annotations

import sqlite3
from typing import Any

from .db import init_databases, learning_connection

DEFAULT_MAX_CALLS = 7
DEFAULT_MAX_PREDICT_TOKENS = 9000
DEFAULT_GLOBAL_MAX_CALLS_PER_DAY = 1000
DEFAULT_GLOBAL_MAX_PREDICT_TOKENS_PER_DAY = 1_000_000


class LearningBudgetError(ValueError):
    """The durable learning budget could not be reserved consistently."""


def reserve_job_budget(
    job_id: int,
    stage: str,
    *,
    calls: int,
    predict_tokens: int,
    max_calls: int = DEFAULT_MAX_CALLS,
    max_predict_tokens: int = DEFAULT_MAX_PREDICT_TOKENS,
    max_global_calls_per_day: int = DEFAULT_GLOBAL_MAX_CALLS_PER_DAY,
    max_global_predict_tokens_per_day: int = DEFAULT_GLOBAL_MAX_PREDICT_TOKENS_PER_DAY,
) -> dict[str, Any]:
    """Atomically reserve a stage once against per-job and daily global ceilings."""
    for name, value, minimum, maximum in (
        ("job_id", job_id, 1, 2**63 - 1),
        ("calls", calls, 0, 20),
        ("predict_tokens", predict_tokens, 0, 100000),
        ("max_calls", max_calls, 1, 20),
        ("max_predict_tokens", max_predict_tokens, 1, 100000),
        ("max_global_calls_per_day", max_global_calls_per_day, 1, 100000),
        ("max_global_predict_tokens_per_day", max_global_predict_tokens_per_day, 1, 100000000),
    ):
        if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
            raise LearningBudgetError(f"{name} is outside the permitted integer range.")
    if not isinstance(stage, str) or not stage.strip() or len(stage.strip()) > 80:
        raise LearningBudgetError("stage must contain 1–80 characters.")
    stage = stage.strip()
    if calls == 0 and predict_tokens != 0:
        raise LearningBudgetError("A zero-call reservation cannot reserve output tokens.")
    if calls > 0 and predict_tokens == 0 and not stage.startswith("sandbox_execution_"):
        raise LearningBudgetError("Model call reservations must reserve a positive token ceiling.")

    init_databases()
    try:
        with learning_connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """INSERT OR IGNORE INTO learning_pipeline_budgets(
                       job_id, max_calls, max_predict_tokens
                   ) VALUES (?, ?, ?)""",
                (job_id, max_calls, max_predict_tokens),
            )
            budget = connection.execute(
                "SELECT max_calls, max_predict_tokens FROM learning_pipeline_budgets WHERE job_id=?",
                (job_id,),
            ).fetchone()
            if budget is None:
                raise LearningBudgetError("Budget row could not be created.")
            if int(budget["max_calls"]) != max_calls or int(budget["max_predict_tokens"]) != max_predict_tokens:
                raise LearningBudgetError("Job budget ceilings are immutable.")

            existing = connection.execute(
                """SELECT calls_reserved, predict_tokens_reserved
                   FROM learning_pipeline_budget_reservations WHERE job_id=? AND stage=?""",
                (job_id, stage),
            ).fetchone()
            if existing is not None:
                if int(existing["calls_reserved"]) != calls or int(existing["predict_tokens_reserved"]) != predict_tokens:
                    raise LearningBudgetError("A budget stage cannot be replayed with different reservations.")
                return {
                    "reserved": True, "already_reserved": True,
                    "stage": stage, **budget_snapshot(connection, job_id),
                }

            # SQLite date('now') is UTC. Persist the day's ceilings once so callers
            # cannot raise the shared cap midway through a day by changing arguments.
            day = connection.execute("SELECT date('now')").fetchone()[0]
            connection.execute(
                """INSERT OR IGNORE INTO learning_global_daily_budgets(
                       budget_date, max_calls, max_predict_tokens
                   ) VALUES (?, ?, ?)""",
                (day, max_global_calls_per_day, max_global_predict_tokens_per_day),
            )
            global_budget = connection.execute(
                """SELECT max_calls, max_predict_tokens
                   FROM learning_global_daily_budgets WHERE budget_date=?""",
                (day,),
            ).fetchone()
            if (
                global_budget is None
                or int(global_budget["max_calls"]) != max_global_calls_per_day
                or int(global_budget["max_predict_tokens"]) != max_global_predict_tokens_per_day
            ):
                raise LearningBudgetError("Daily global budget ceilings are immutable once initialized.")

            global_totals = connection.execute(
                """SELECT COALESCE(SUM(calls_reserved),0) AS calls_used,
                          COALESCE(SUM(predict_tokens_reserved),0) AS tokens_used
                   FROM learning_global_budget_reservations WHERE budget_date=?""",
                (day,),
            ).fetchone()
            global_calls = int(global_totals["calls_used"])
            global_tokens = int(global_totals["tokens_used"])
            if (
                global_calls + calls > max_global_calls_per_day
                or global_tokens + predict_tokens > max_global_predict_tokens_per_day
            ):
                return {
                    "reserved": False, "already_reserved": False, "stage": stage,
                    "reason": "global_budget_exhausted", **budget_snapshot(connection, job_id),
                }

            totals = connection.execute(
                """SELECT COALESCE(SUM(calls_reserved),0) AS calls_used,
                          COALESCE(SUM(predict_tokens_reserved),0) AS tokens_used
                   FROM learning_pipeline_budget_reservations WHERE job_id=?""",
                (job_id,),
            ).fetchone()
            calls_used = int(totals["calls_used"])
            tokens_used = int(totals["tokens_used"])
            if calls_used + calls > max_calls or tokens_used + predict_tokens > max_predict_tokens:
                return {
                    "reserved": False, "already_reserved": False, "stage": stage,
                    "reason": "budget_exhausted", **budget_snapshot(connection, job_id),
                }

            connection.execute(
                """INSERT INTO learning_pipeline_budget_reservations(
                       job_id, stage, calls_reserved, predict_tokens_reserved
                   ) VALUES (?, ?, ?, ?)""",
                (job_id, stage, calls, predict_tokens),
            )
            connection.execute(
                """INSERT INTO learning_global_budget_reservations(
                       budget_date, job_id, stage, calls_reserved, predict_tokens_reserved
                   ) VALUES (?, ?, ?, ?, ?)""",
                (day, job_id, stage, calls, predict_tokens),
            )
            return {
                "reserved": True, "already_reserved": False, "stage": stage,
                **budget_snapshot(connection, job_id),
            }
    except LearningBudgetError:
        raise
    except sqlite3.Error as exc:
        raise LearningBudgetError("Durable learning budget storage is unavailable.") from exc


def _global_budget_snapshot(connection) -> dict[str, Any]:
    day = connection.execute("SELECT date('now')").fetchone()[0]
    budget = connection.execute(
        """SELECT max_calls, max_predict_tokens FROM learning_global_daily_budgets
           WHERE budget_date=?""",
        (day,),
    ).fetchone()
    if budget is None:
        return {
            "global_budget_date_utc": day,
            "global_max_calls_per_day": DEFAULT_GLOBAL_MAX_CALLS_PER_DAY,
            "global_calls_reserved_today": 0,
            "global_max_predict_tokens_per_day": DEFAULT_GLOBAL_MAX_PREDICT_TOKENS_PER_DAY,
            "global_predict_tokens_reserved_today": 0,
            "global_budget_exhausted": False,
        }
    totals = connection.execute(
        """SELECT COALESCE(SUM(calls_reserved),0) AS calls_used,
                  COALESCE(SUM(predict_tokens_reserved),0) AS tokens_used
           FROM learning_global_budget_reservations WHERE budget_date=?""",
        (day,),
    ).fetchone()
    calls_used = int(totals["calls_used"])
    tokens_used = int(totals["tokens_used"])
    return {
        "global_budget_date_utc": day,
        "global_max_calls_per_day": int(budget["max_calls"]),
        "global_calls_reserved_today": calls_used,
        "global_max_predict_tokens_per_day": int(budget["max_predict_tokens"]),
        "global_predict_tokens_reserved_today": tokens_used,
        "global_budget_exhausted": (
            calls_used >= int(budget["max_calls"])
            or tokens_used >= int(budget["max_predict_tokens"])
        ),
    }


def budget_snapshot(connection, job_id: int) -> dict[str, Any]:
    budget = connection.execute(
        "SELECT max_calls, max_predict_tokens FROM learning_pipeline_budgets WHERE job_id=?",
        (job_id,),
    ).fetchone()
    if budget is None:
        snapshot = {
            "max_calls": DEFAULT_MAX_CALLS, "calls_used": 0,
            "max_predict_tokens": DEFAULT_MAX_PREDICT_TOKENS,
            "predict_tokens_reserved": 0, "billing_cost_known": False,
        }
    else:
        totals = connection.execute(
            """SELECT COALESCE(SUM(calls_reserved),0) AS calls_used,
                      COALESCE(SUM(predict_tokens_reserved),0) AS tokens_used
               FROM learning_pipeline_budget_reservations WHERE job_id=?""",
            (job_id,),
        ).fetchone()
        snapshot = {
            "max_calls": int(budget["max_calls"]),
            "calls_used": int(totals["calls_used"]),
            "max_predict_tokens": int(budget["max_predict_tokens"]),
            "predict_tokens_reserved": int(totals["tokens_used"]),
            "budget_exhausted": (
                int(totals["calls_used"]) >= int(budget["max_calls"])
                or int(totals["tokens_used"]) >= int(budget["max_predict_tokens"])
            ),
            "billing_cost_known": False,
            "scope": "durable_single_learning_job_across_generation_and_review_channels",
        }
    snapshot.update(_global_budget_snapshot(connection))
    return snapshot


def get_job_budget(job_id: int) -> dict[str, Any]:
    """Read the current durable budget without creating or changing it."""
    init_databases()
    with learning_connection() as connection:
        return budget_snapshot(connection, job_id)
