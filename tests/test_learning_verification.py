import hashlib
import json

from app import db, sandbox
from app.learning_verification import (
    count_doctest_examples, extract_source_assertion_blocks, run_candidate_source_doctests,
)
from app.knowledge_review import get_candidate_review_summary


def _candidate(tmp_path, monkeypatch, lesson, source_text=None):
    monkeypatch.setattr(db.defaults, "settings_db_path", str(tmp_path / "settings.db"))
    monkeypatch.setattr(db.defaults, "learning_db_path", str(tmp_path / "learning.db"))
    db.init_databases()
    from app import learning
    job = learning.create_job(
        "doctest verification",
        source_text=lesson if source_text is None else source_text,
    )
    digest = hashlib.sha256(lesson.encode("utf-8")).hexdigest()
    with db.learning_connection() as conn:
        cursor = conn.execute(
            """INSERT INTO temporary_knowledge_candidates(
                   job_id, topic, content, source_kind, source_urls_json,
                   content_sha256, quality_report_json, status
               ) VALUES (?, ?, ?, ?, ?, ?, ?, 'awaiting_test')""",
            (job["id"], job["topic"], lesson, "web_retrieved",
             json.dumps(["https://docs.example.org/tutorial"]), digest,
             json.dumps({"accepted": True})),
        )
    return int(cursor.lastrowid)


def test_count_doctest_examples_ignores_non_examples():
    assert count_doctest_examples("Example: 1 + 1 = 2") == 0
    assert count_doctest_examples(">>> 1 + 1\n2\n>>> len('abc')\n3") == 2


def test_model_generated_examples_are_not_used_as_their_own_oracle(tmp_path, monkeypatch):
    candidate_id = _candidate(
        tmp_path, monkeypatch,
        "Model-generated lesson:\\n\\n>>> 1 + 1\\n2\\n",
        source_text="Original source contains no executable example.",
    )
    result = run_candidate_source_doctests(candidate_id)
    assert result["status"] == "no_examples"
    assert result["promoted"] is False
    with db.learning_connection() as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM knowledge_test_runs WHERE candidate_id=?", (candidate_id,)
        ).fetchone()[0] == 0


def test_no_doctest_does_not_count_as_a_pass(tmp_path, monkeypatch):
    candidate_id = _candidate(tmp_path, monkeypatch, "A concise lesson without executable examples.")
    result = run_candidate_source_doctests(candidate_id)
    assert result["status"] == "no_examples"
    assert result["promoted"] is False
    review = get_candidate_review_summary(candidate_id)
    assert any(
        item["channel"] == "sandbox" and item["status"] == "no_examples"
        for item in review["channel_attempts"]
    )
    with db.learning_connection() as conn:
        status = conn.execute(
            "SELECT status FROM temporary_knowledge_candidates WHERE id=?", (candidate_id,)
        ).fetchone()[0]
    assert status == "awaiting_test"


def test_source_doctest_runs_in_sandbox_without_promoting_whole_lesson(tmp_path, monkeypatch):
    lesson = "A runnable example:\n\n>>> 1 + 1\n2\n"
    candidate_id = _candidate(tmp_path, monkeypatch, lesson)
    monkeypatch.setenv("SANDBOX_PYTHON_IMAGE", "python:3.11-slim@sha256:" + "a" * 64)
    monkeypatch.setenv("SANDBOX_DOCKER_EXECUTABLE", "/usr/bin/docker")

    def fake_run_python(source_code, **kwargs):
        assert "DocTestParser" in source_code
        assert kwargs["timeout_seconds"] == 15
        assert kwargs["image"].endswith("a" * 64)
        return sandbox.SandboxResult(
            status="succeeded", exit_code=0,
            stdout='{"doctest_attempted": 1, "doctest_failed": 0, "assertion_blocks_attempted": 0, "assertion_blocks_failed": 0}\n',
            stderr="", duration_ms=12,
        )

    monkeypatch.setattr("app.learning_verification.sandbox.run_python", fake_run_python)
    result = run_candidate_source_doctests(candidate_id)
    assert result["passed"] is True
    assert result["promoted"] is False
    assert result["verification_scope"] == "explicit doctest examples in this lesson only"
    assert "not sufficient to promote" in result["note"]
    with db.learning_connection() as conn:
        row = conn.execute(
            "SELECT status FROM temporary_knowledge_candidates WHERE id=?", (candidate_id,)
        ).fetchone()
        test_run = conn.execute(
            "SELECT run_status, suite_name FROM knowledge_test_runs WHERE candidate_id=?",
            (candidate_id,),
        ).fetchone()
    assert row["status"] == "awaiting_test"
    assert test_run["run_status"] == "passed"
    assert test_run["suite_name"] == "source-doctest-suite"
    summary = get_candidate_review_summary(candidate_id)
    assert summary["confidence"]["components"]["scoped_source_example_tests"] == 5
    assert summary["test_runs"][0]["run_status"] == "passed"
    assert summary["verification_status"] == "partial_scope_tested"


def test_sandbox_unavailable_keeps_candidate_temporary(tmp_path, monkeypatch):
    candidate_id = _candidate(tmp_path, monkeypatch, ">>> 1 + 1\n2")
    monkeypatch.delenv("SANDBOX_PYTHON_IMAGE", raising=False)
    monkeypatch.delenv("SANDBOX_DOCKER_EXECUTABLE", raising=False)
    monkeypatch.setattr("app.learning_verification.shutil.which", lambda _: None)
    result = run_candidate_source_doctests(candidate_id)
    assert result["status"] == "sandbox_unconfigured"
    assert result["promoted"] is False
    review = get_candidate_review_summary(candidate_id)
    assert any(
        item["channel"] == "sandbox" and item["status"] == "sandbox_unconfigured"
        for item in review["channel_attempts"]
    )
    with db.learning_connection() as conn:
        assert conn.execute(
            "SELECT status FROM temporary_knowledge_candidates WHERE id=?", (candidate_id,)
        ).fetchone()[0] == "awaiting_test"


def test_failed_source_example_is_saved_as_learning_experience(tmp_path, monkeypatch):
    candidate_id = _candidate(tmp_path, monkeypatch, ">>> 1 + 1\\n3")
    monkeypatch.setenv("SANDBOX_PYTHON_IMAGE", "python:3.11-slim@sha256:" + "b" * 64)
    monkeypatch.setenv("SANDBOX_DOCKER_EXECUTABLE", "/usr/bin/docker")

    def fake_run_python(source_code, **kwargs):
        return sandbox.SandboxResult(
            status="failed", exit_code=1,
            stdout='{"attempted": 1, "failed": 1}\\n',
            stderr="Expected 3 but got 2", duration_ms=9,
        )

    monkeypatch.setattr("app.learning_verification.sandbox.run_python", fake_run_python)
    result = run_candidate_source_doctests(candidate_id)
    assert result["passed"] is False
    assert result["promoted"] is False
    assert result["failure_experience_id"] is not None
    with db.learning_connection() as conn:
        run = conn.execute(
            "SELECT run_status FROM knowledge_test_runs WHERE candidate_id=?", (candidate_id,)
        ).fetchone()
        experience = conn.execute(
            "SELECT status, test_results_json FROM learning_experiences WHERE id=?",
            (result["failure_experience_id"],),
        ).fetchone()
    assert run["run_status"] == "failed"
    assert experience["status"] == "failed"
    assert "Expected 3 but got 2" in experience["test_results_json"]


def test_extract_source_assertion_blocks_rejects_imports_and_unsafe_constructs():
    source = """
```python
def add(left, right):
    return left + right
assert add(2, 3) == 5
```

```python
import os
assert os.path.exists("/")
```

```python
while True:
    assert True
```

```python
value = 3
assert value == 3
```
"""
    blocks = extract_source_assertion_blocks(source)
    assert len(blocks) == 2
    assert "def add" in blocks[0]
    assert "value == 3" in blocks[1]


def test_fenced_source_assertions_run_in_sandbox_without_promoting_lesson(tmp_path, monkeypatch):
    source = "Example:\n\n```python\ndef add(a, b):\n    return a + b\nassert add(2, 3) == 5\n```\n"
    candidate_id = _candidate(tmp_path, monkeypatch, source)
    monkeypatch.setenv("SANDBOX_PYTHON_IMAGE", "python:3.11-slim@sha256:" + "c" * 64)
    monkeypatch.setenv("SANDBOX_DOCKER_EXECUTABLE", "/usr/bin/docker")

    def fake_run_python(source_code, **kwargs):
        assert "assertion_blocks_attempted" in source_code
        assert kwargs["timeout_seconds"] == 15
        return sandbox.SandboxResult(
            status="succeeded", exit_code=0,
            stdout='{"doctest_attempted": 0, "doctest_failed": 0, "assertion_blocks_attempted": 1, "assertion_blocks_failed": 0}\n',
            stderr="", duration_ms=15,
        )

    monkeypatch.setattr("app.learning_verification.sandbox.run_python", fake_run_python)
    result = run_candidate_source_doctests(candidate_id)

    assert result["passed"] is True
    assert result["tests"] == 1
    assert result["assertion_blocks"] == 1
    assert result["promoted"] is False
    with db.learning_connection() as conn:
        status = conn.execute(
            "SELECT status FROM temporary_knowledge_candidates WHERE id=?", (candidate_id,)
        ).fetchone()[0]
    assert status == "awaiting_test"
