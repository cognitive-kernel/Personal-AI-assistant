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
             json.dumps(["https://docs.python.org/3/tutorial/index.html"]), digest,
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
    lesson = "A runnable example:\n\n>>> 1 + 1\n2\n",
    )
    with db.learning_connection() as conn:
        conn.execute(
            "UPDATE temporary_knowledge_candidates SET source_urls_json=? WHERE id=?",
            (json.dumps(["https://python.org.attacker.example/tutorial"]), candidate_id),
        )

    result = run_candidate_source_doctests(candidate_id)

    assert result["status"] == "missing_source_oracle"
    assert result["tests"] == 0
    assert result["promoted"] is False
    with db.learning_connection() as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM knowledge_test_runs WHERE candidate_id=?", (candidate_id,)
        ).fetchone()[0] == 0
