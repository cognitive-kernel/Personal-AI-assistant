from __future__ import annotations

import sqlite3

from app.learning_observability import collect_learning_operational_metrics


def _connection():
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.executescript("""
        CREATE TABLE learning_jobs(status TEXT, updated_at TEXT);
        CREATE TABLE learning_candidate_channel_runs(status TEXT, updated_at TEXT);
        CREATE TABLE learning_global_daily_budgets(
            budget_date TEXT PRIMARY KEY, max_calls INTEGER, max_predict_tokens INTEGER
        );
        CREATE TABLE learning_global_budget_reservations(
            budget_date TEXT, calls_reserved INTEGER, predict_tokens_reserved INTEGER
        );
    """)
    return connection


def test_release_gate_never_calls_small_samples_healthy():
    connection = _connection()
    result = collect_learning_operational_metrics(connection)
    assert result["release_gate"] == "insufficient_data"
    assert result["learning_jobs"]["failure_rate"] is None
    assert result["review_channels"]["failure_rate"] is None
    assert result["alerts"] == []


def test_release_gate_blocks_on_failure_rate_and_daily_budget_pressure():
    connection = _connection()
    for index in range(10):
        connection.execute(
            "INSERT INTO learning_jobs VALUES (?, datetime('now'))",
            ("failed" if index < 3 else "completed",),
        )
        connection.execute(
            "INSERT INTO learning_candidate_channel_runs VALUES (?, datetime('now'))",
            ("failed" if index < 4 else "succeeded",),
        )
    day = connection.execute("SELECT date('now')").fetchone()[0]
    connection.execute(
        "INSERT INTO learning_global_daily_budgets VALUES (?, 100, 1000)", (day,)
    )
    connection.execute(
        "INSERT INTO learning_global_budget_reservations VALUES (?, 95, 200)", (day,)
    )
    result = collect_learning_operational_metrics(connection)
    codes = {item["code"] for item in result["alerts"]}
    assert result["release_gate"] == "blocked"
    assert "learning_job_failure_rate" in codes
    assert "review_channel_failure_rate" in codes
    assert "daily_budget_utilization" in codes


def test_release_gate_passes_only_when_both_failure_samples_are_sufficient():
    connection = _connection()
    for _ in range(10):
        connection.execute("INSERT INTO learning_jobs VALUES ('completed', datetime('now'))")
        connection.execute("INSERT INTO learning_candidate_channel_runs VALUES ('succeeded', datetime('now'))")
    result = collect_learning_operational_metrics(connection)
    assert result["release_gate"] == "pass"
    assert result["alerts"] == []


def test_invalid_release_thresholds_are_rejected():
    connection = _connection()
    try:
        collect_learning_operational_metrics(connection, min_terminal_sample=0)
    except ValueError:
        pass
    else:
        raise AssertionError("non-positive sample threshold should be rejected")
