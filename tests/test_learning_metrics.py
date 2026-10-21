from app import db, learning
from app.learning_metrics import get_learning_metrics


def test_learning_metrics_summarize_trust_failures_and_successful_alternatives(tmp_path, monkeypatch):
    monkeypatch.setattr(db.defaults, "settings_db_path", str(tmp_path / "settings.db"))
    monkeypatch.setattr(db.defaults, "learning_db_path", str(tmp_path / "learning.db"))
    monkeypatch.setattr(db.defaults, "cloud_resources_db_path", str(tmp_path / "cloud_resources.db"))
    db.init_databases()

    tested_job = learning.create_job("metrics tested knowledge", source_text="Retained source.")
    failed_job = learning.create_job("metrics failed knowledge", source_text="Retained failure source.")
    with db.learning_connection() as conn:
        conn.execute(
            """INSERT INTO knowledge_items(
                   topic, content, source_kind, version, status, job_id,
                   trust_level, learning_status, domain, source_url, raw_content
               ) VALUES ('tested topic', 'tested content', 'user_provided_text', 1, 'verified', ?,
                         'L2', 'tested', 'python', 'https://example.org/source', 'retained source')""",
            (tested_job["id"],),
        )
        conn.execute(
            """INSERT INTO knowledge_items(
                   topic, content, source_kind, version, status, job_id,
                   trust_level, learning_status, domain, source_url, raw_content
               ) VALUES ('old topic', 'old content', 'user_provided_text', 1, 'superseded', ?,
                         'L-1', 'superseded', 'python', 'https://example.org/old', 'old source')""",
            (failed_job["id"],),
        )
        conn.execute(
            """INSERT INTO temporary_knowledge_candidates(
                   job_id, topic, content, source_kind, source_urls_json,
                   content_sha256, quality_report_json, status, trust_level, learning_status
               ) VALUES (?, 'failed topic', 'failed claim', 'user_provided_text', '[]',
                         ?, '{}', 'failed', 'L-1', 'failed')""",
            (failed_job["id"], "a" * 64),
        )
        conn.execute(
            """INSERT INTO learning_experiences(
                   topic, problem, hypothesis, status, source_evidence_json,
                   test_plan_json, test_results_json, takeaway, experience_type
               ) VALUES
                   ('topic', 'test failure', 'diagnose', 'failed', '[]', '[]', '{}', 'failure', 'test_failure'),
                   ('topic', 'successful alternative', 'corrected', 'verified', '[]', '[]', '{}', 'replacement passed', 'successful_alternative')"""
        )

    metrics = get_learning_metrics()

    assert metrics["active_knowledge"] == {
        "total": 1, "tested_or_human_verified": 1, "tested_share": 1.0,
    }
    assert metrics["failure_events_total"] == 1
    assert metrics["successful_alternatives_total"] == 1
    assert metrics["methodology"]["monthly_values_are_counts_not_failure_rates"] is True
    assert metrics["monthly_experience_events_last_365_days"][-1]["failure_events"] == 1
    assert any(
        row["status"] == "failed" and row["trust_level"] == "L-1" and row["count"] == 1
        for row in metrics["candidate_states"]
    )


def test_learning_metrics_report_empty_denominators_without_inventing_zero_percent(tmp_path, monkeypatch):
    monkeypatch.setattr(db.defaults, "settings_db_path", str(tmp_path / "settings.db"))
    monkeypatch.setattr(db.defaults, "learning_db_path", str(tmp_path / "learning.db"))
    monkeypatch.setattr(db.defaults, "cloud_resources_db_path", str(tmp_path / "cloud_resources.db"))
    db.init_databases()

    metrics = get_learning_metrics()

    assert metrics["active_knowledge"] == {
        "total": 0, "tested_or_human_verified": 0, "tested_share": None,
    }
    assert metrics["average_hours_to_tested_alternative"] is None
