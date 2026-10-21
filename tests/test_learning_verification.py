import hashlib
import json

from app import db, sandbox
from app.learning_verification import count_doctest_examples, run_candidate_source_doctests


def _candidate(tmp_path, monkeypatch, lesson):
    monkeypatch.setattr(db.defaults, "settings_db_path", str(tmp_path / "settings.db"))
    monkeypatch.setattr(db.defaults, "learning_db_path", str(tmp_path / "learning.db"))
    db.init_databases()
    job_id = db.learning_connection
    from app import learning
    job = learning.create_job("doctest verification", source_text="source lesson")
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


def test_no_doctest_does_not_count_as_a_pass(tmp_path, monkeypatch):
    candidate_id = _candidate(tmp_path, monkeypatch, "A concise lesson without executable examples.")
    result = run_candidate_source_doctests(candidate_id)
    assert result["status"] == "no_examples"
    assert result["promoted"] is False
    with db.learning_connection() as conn:
        status = conn.execute(
            "SELECT status FROM temporary_knowledge_candidates WHERE id=?", (candidate_id,)
        ).fetchone()[0]
    assert status == "awaiting_test"


def test_source_doctest_runs_in_sandbox_and_promotes_only_when_examples_pass(tmp_path, monkeypatch):
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
            stdout='{"attempted": 1, "failed": 0}\n',
            stderr="", duration_ms=12,
        )

    monkeypatch.setattr("app.learning_verification.sandbox.run_python", fake_run_python)
    result = run_candidate_source_doctests(candidate_id)
    assert result["passed"] is True
    assert result["promoted"] is True
    assert result["verification_scope"] == "explicit doctest examples in this lesson only"
    with db.learning_connection() as conn:
        row = conn.execute(
            "SELECT status, test_results_json FROM temporary_knowledge_candidates WHERE id=?",
            (candidate_id,),
        ).fetchone()
    assert row["status"] == "passed"
    assert "source-doctest-suite" in row["test_results_json"]


def test_sandbox_unavailable_keeps_candidate_temporary(tmp_path, monkeypatch):
    candidate_id = _candidate(tmp_path, monkeypatch, ">>> 1 + 1\n2")
    monkeypatch.delenv("SANDBOX_PYTHON_IMAGE", raising=False)
    monkeypatch.delenv("SANDBOX_DOCKER_EXECUTABLE", raising=False)
    monkeypatch.setattr("app.learning_verification.shutil.which", lambda _: None)
    result = run_candidate_source_doctests(candidate_id)
    assert result["status"] == "sandbox_unconfigured"
    assert result["promoted"] is False
    with db.learning_connection() as conn:
        assert conn.execute(
            "SELECT status FROM temporary_knowledge_candidates WHERE id=?", (candidate_id,)
        ).fetchone()[0] == "awaiting_test"
