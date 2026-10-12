"""Restricted, disposable SQLite runner for read-only examples over synthetic data."""
from __future__ import annotations

import re
import sqlite3
import time
from typing import Any


_MAX_SQL_CHARS = 10_000
_MAX_ROWS = 200
_ALLOWED_FUNCTIONS = {
    "abs", "avg", "coalesce", "count", "date", "datetime", "ifnull", "length",
    "lower", "max", "min", "printf", "round", "strftime", "substr", "sum", "typeof", "upper",
}


def _synthetic_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE users(id INTEGER PRIMARY KEY, name TEXT NOT NULL, region TEXT NOT NULL);
        CREATE TABLE orders(id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, total REAL NOT NULL, status TEXT NOT NULL);
        INSERT INTO users(id, name, region) VALUES
            (1, 'Ava', 'north'), (2, 'Ben', 'south'), (3, 'Cy', 'north');
        INSERT INTO orders(id, user_id, total, status) VALUES
            (101, 1, 25.50, 'paid'), (102, 1, 10.00, 'pending'),
            (103, 2, 80.00, 'paid'), (104, 3, 12.00, 'cancelled');
    """)
    conn.execute("PRAGMA query_only=ON")
    return conn


def run_sql(query: str, *, timeout_seconds: float = 1.0, max_rows: int = 100) -> dict[str, Any]:
    """Execute one read-only statement against fresh synthetic tables; never touches app data."""
    if not isinstance(query, str) or not query.strip():
        raise ValueError("SQL query must be a non-empty string.")
    if len(query) > _MAX_SQL_CHARS:
        raise ValueError(f"SQL query must be at most {_MAX_SQL_CHARS} characters.")
    if not re.match(r"^(SELECT|WITH)\b", query.lstrip(), flags=re.IGNORECASE):
        raise ValueError("Only SELECT or WITH queries are supported by the sandbox.")
    if not 0.05 <= float(timeout_seconds) <= 5.0:
        raise ValueError("timeout_seconds must be between 0.05 and 5 seconds.")
    if not 1 <= int(max_rows) <= _MAX_ROWS:
        raise ValueError(f"max_rows must be between 1 and {_MAX_ROWS}.")

    conn = _synthetic_connection()
    deadline = time.monotonic() + float(timeout_seconds)

    def authorize(action: int, arg1: str | None, arg2: str | None, _db: str | None, _trigger: str | None) -> int:
        allowed = {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_RECURSIVE}
        if action in allowed:
            return sqlite3.SQLITE_OK
        if action == sqlite3.SQLITE_FUNCTION:
            return sqlite3.SQLITE_OK if (arg2 or arg1 or "").lower() in _ALLOWED_FUNCTIONS else sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_DENY

    conn.set_authorizer(authorize)
    conn.set_progress_handler(lambda: 1 if time.monotonic() > deadline else 0, 1000)
    try:
        cursor = conn.execute(query)
        columns = [column[0] for column in (cursor.description or [])]
        rows = cursor.fetchmany(int(max_rows) + 1)
        if len(rows) > int(max_rows):
            raise ValueError(f"Query exceeded the {max_rows}-row result limit.")
        return {"columns": columns, "rows": [[row[column] for column in columns] for row in rows],
                "row_count": len(rows), "database": "in_memory_synthetic", "read_only": True}
    except sqlite3.Error as exc:
        message = "SQL execution timed out." if "interrupt" in str(exc).lower() else "SQL query rejected or invalid in the restricted sandbox."
        raise ValueError(message) from exc
    finally:
        conn.close()
