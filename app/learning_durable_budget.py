"""Durable idempotent budget shared by lesson generation and candidate review channels.

The budget is scoped to one learning job and survives worker retries/restarts. It
caps requested model calls and output-token ceilings, not provider billing.
"""
from __future__ import annotations

import sqlite3
from typing import Any

from .db import init_databases, learning_connection

DEFAULT_MAX_CALLS = 7
DEFAULT_MAX_PREDICT_TOKENS = 9000


class LearningBudgetError(ValueError):
    """The durable budget could not be reserved consistently."""


def reserve_job_budget(
    job_id: int,
    stage: str,
    *,
    calls: int,
    predict_tokens: int,
    max_calls: int = DEFAULT_MAX_CALLS,
    max_predict_tokens: int = DEFAULT_MAX_PREDICT_TOKENS,
) -> dict[str, Any]:
    """Atomically reserve a stage once; duplicate retries do not consume budget."""
    for name, value, minimum, maximum in (
        ("job_id", job_id, 1, 2**63 - 1),
        ("calls", calls, 0, 20),
        ("predict_tokens", predict_tokens, 0, 100000),
        ("max_calls", max_calls, 1, 20),
        ("max_predict_tokens", max_predict_tokens, 1, 100000),
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
            return {
                "reserved": True, "already_reserved": False, "stage": stage,
                **budget_snapshot(connection, job_id),
            }
    except LearningBudgetError:
        raise
    except sqlite3.Error as exc:
        raise LearningBudgetError("Durable learning budget storage is unavailable.") from exc


def budget_snapshot(connection, job_id: int) -> dict[str, Any]:
    budget = connection.execute(
        "SELECT max_calls, max_predict_tokens FROM learning_pipeline_budgets WHERE job_id=?",
        (job_id,),
    ).fetchone()
    if budget is None:
        return {
            "max_calls": DEFAULT_MAX_CALLS, "calls_used": 0,
            "max_predict_tokens": DEFAULT_MAX_PREDICT_TOKENS,
            "predict_tokens_reserved": 0, "billing_cost_known": False,
        }
    totals = connection.execute(
        """SELECT COALESCE(SUM(calls_reserved),0) AS calls_used,
                  COALESCE(SUM(predict_tokens_reserved),0) AS tokens_used
           FROM learning_pipeline_budget_reservations WHERE job_id=?""",
        (job_id,),
    ).fetchone()
    return {
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


def get_job_budget(job_id: int) -> dict[str, Any]:
    """Read the current durable budget without creating or changing it."""
    init_databases()
    with learning_connection() as connection:
        return budget_snapshot(connection, job_id)
