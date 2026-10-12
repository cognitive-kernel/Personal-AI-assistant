import pytest

from app.sql_sandbox import run_sql


def test_sql_sandbox_runs_select_on_synthetic_data_only():
    result = run_sql("SELECT region, COUNT(*) AS n FROM users GROUP BY region ORDER BY region")
    assert result["database"] == "in_memory_synthetic"
    assert result["read_only"] is True
    assert result["rows"] == [["north", 2], ["south", 1]]


def test_sql_sandbox_supports_ctes_and_joins():
    result = run_sql("WITH paid AS (SELECT user_id, total FROM orders WHERE status='paid') SELECT users.name, SUM(paid.total) FROM users JOIN paid ON users.id=paid.user_id GROUP BY users.name ORDER BY users.name")
    assert result["rows"] == [["Ava", 25.5], ["Ben", 80.0]]


@pytest.mark.parametrize("query", [
    "DELETE FROM users",
    "UPDATE users SET name='x'",
    "INSERT INTO users VALUES (4, 'D', 'west')",
    "PRAGMA database_list",
    "ATTACH DATABASE 'file.db' AS other",
    "SELECT load_extension('evil')",
])
def test_sql_sandbox_rejects_writes_and_unsafe_operations(query):
    with pytest.raises(ValueError):
        run_sql(query)


def test_sql_sandbox_limits_result_rows_and_query_size():
    with pytest.raises(ValueError, match="result limit"):
        run_sql("SELECT 1 AS n UNION ALL SELECT 2 UNION ALL SELECT 3", max_rows=2)
    with pytest.raises(ValueError, match="characters"):
        run_sql("SELECT 1 -- " + "x" * 10_000)


def test_sql_sandbox_rejects_invalid_limits():
    with pytest.raises(ValueError, match="timeout_seconds"):
        run_sql("SELECT 1", timeout_seconds=10)
    with pytest.raises(ValueError, match="max_rows"):
        run_sql("SELECT 1", max_rows=201)
