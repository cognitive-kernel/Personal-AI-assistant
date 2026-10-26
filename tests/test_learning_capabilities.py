import pytest

from app import db, experience, learning_capabilities, task_manager, usage_metrics, learning
from app.db import learning_connection


def _setup(tmp_path, monkeypatch):
    monkeypatch.setattr(db.defaults, "settings_db_path", str(tmp_path / "settings.db"))
    monkeypatch.setattr(db.defaults, "learning_db_path", str(tmp_path / "learning.db"))
    monkeypatch.setattr(db.defaults, "cloud_resources_db_path", str(tmp_path / "cloud_resources.db"))
    db.init_databases()
    with learning_connection() as connection:
        path_id = connection.execute(
            "INSERT INTO learning_paths(curriculum_slug, title) VALUES ('capability-test', 'Capability test')"
        ).lastrowid
        topic_ids = []
        for position, (topic, status) in enumerate(
            [("Not assessed", "pending"), ("Weak topic", "completed"), ("Strong topic", "completed"), ("Invalid topic", "completed")],
            start=1,
        ):
            topic_id = connection.execute(
                """INSERT INTO learning_path_topics(path_id, topic, position, status)
                   VALUES (?, ?, ?, ?)""",
                (path_id, topic, position, status),
            ).lastrowid
            topic_ids.append(int(topic_id))
        for topic_id, topic in zip(topic_ids[1:], ["Weak topic", "Strong topic", "Invalid topic"]):
            job_id = connection.execute(
                """INSERT INTO learning_jobs(topic, status, source_kind, path_topic_id)
                   VALUES (?, 'completed', 'model_knowledge_no_external_source', ?)""",
                (topic, topic_id),
            ).lastrowid
            if topic == "Weak topic":
                attempts = [(45.0, 1, 3, "normalized_exact_match")]
            elif topic == "Strong topic":
                attempts = [
                    (85.0, 2, 3, "normalized_exact_match"),
                    (92.0, 3, 3, "normalized_exact_match"),
                ]
            else:
                attempts = [(100.0, 3, 3, "unreviewed_scoring_method")]
            for score, correct, questions, method in attempts:
                connection.execute(
                    """INSERT INTO assessment_attempts(
                           job_id, answers_json, score, correct_count, question_count, scoring_method
                       ) VALUES (?, '[]', ?, ?, ?, ?)""",
                    (job_id, score, correct, questions, method),
                )
    return int(path_id), topic_ids


def test_capability_map_distinguishes_unassessed_weak_repeated_and_invalid_evidence(tmp_path, monkeypatch):
    path_id, topic_ids = _setup(tmp_path, monkeypatch)

    result = learning_capabilities.get_capability_map(path_id=path_id)

    assert result["count"] == 4
    by_topic = {item["capability"]: item for item in result["capabilities"]}
    assert by_topic["Not assessed"]["status"] == "not_assessed"
    assert by_topic["Not assessed"]["confidence"] == "none"
    assert by_topic["Weak topic"]["status"] == "needs_reinforcement"
    assert by_topic["Weak topic"]["latest_score"] == 45
    assert by_topic["Strong topic"]["status"] == "repeated_provisional"
    assert by_topic["Strong topic"]["assessment_count"] == 2
    assert by_topic["Strong topic"]["latest_score"] == 92
    assert by_topic["Invalid topic"]["status"] == "invalid_evidence"
    assert by_topic["Invalid topic"]["latest_score"] is None
    assert all("not established" in item["evidence_limit"] for item in result["capabilities"])
    assert result["summary"]["not_assessed"] == 1
    assert result["summary"]["repeated_provisional"] == 1


def test_capability_map_validates_scope_and_limit(tmp_path, monkeypatch):
    path_id, _ = _setup(tmp_path, monkeypatch)
    with pytest.raises(KeyError):
        learning_capabilities.get_capability_map(path_id=path_id + 999)
    with pytest.raises(ValueError, match="limit"):
        learning_capabilities.get_capability_map(path_id=path_id, limit=501)
    with pytest.raises(ValueError, match="path_id"):
        learning_capabilities.get_capability_map(path_id=True)



def test_learning_dashboard_exposes_capability_map_control():
    from pathlib import Path

    root = Path(__file__).parents[1] / "app" / "static"
    page = (root / "index.html").read_text(encoding="utf-8")
    script = (root / "learning.js").read_text(encoding="utf-8")
    assert 'id="refresh-capability-map"' in page
    assert 'id="capability-map-output"' in page
    assert '"/api/learning/capabilities?limit=200"' in script
    assert "انتقال مهارت عملی را اثبات نمی‌کند" in script


def test_capability_map_uses_two_distinct_provenanced_sandbox_exercises_as_practical_evidence(tmp_path, monkeypatch):
    path_id, _ = _setup(tmp_path, monkeypatch)
    item = experience.create_experience("Not assessed", "Repeatable practical capability evidence.", "The learner can perform the task.")
    sources = [{
        "url": "https://docs.python.org/3/library/",
        "claim": "Official reference excerpt",
        "excerpt": "A verified excerpt.",
        "live_content_sha256": "a" * 64,
        "excerpt_sha256": "b" * 64,
    }]
    results = [
        {"test": "exercise one", "passed": True, "runner": "docker-sandbox", "source_sha256": "c" * 64,
         "source_evidence": [{"url": sources[0]["url"], "live_content_sha256": "a" * 64, "excerpt_sha256": "b" * 64}]},
        {"test": "exercise two", "passed": True, "runner": "docker-sandbox", "source_sha256": "d" * 64,
         "source_evidence": [{"url": sources[0]["url"], "live_content_sha256": "a" * 64, "excerpt_sha256": "b" * 64}]},
    ]
    experience.update_experience(
        item["id"], status="verified", source_evidence=sources,
        test_plan=[{"test": "exercise one"}, {"test": "exercise two"}],
        test_results=results, _controlled_run=True,
    )

    result = learning_capabilities.get_capability_map(path_id=path_id)
    practical = next(row for row in result["capabilities"] if row["capability"] == "Not assessed")
    assert practical["status"] == "practical_provisional"
    assert practical["practical_repeatable"] is True
    assert practical["practical_attempt_count"] == 2
    assert practical["practical_source_count"] == 2
    assert result["summary"]["practical_provisional"] == 1


def test_capability_map_does_not_count_unprovenanced_or_duplicate_practical_runs(tmp_path, monkeypatch):
    path_id, _ = _setup(tmp_path, monkeypatch)
    item = experience.create_experience("Not assessed", "Insufficient evidence.", "A claim.")
    experience.update_experience(
        item["id"], status="verified",
        source_evidence=[{"url": "https://docs.python.org/3/library/", "claim": "Reference"}],
        test_plan=[{"test": "same"}],
        test_results=[
            {"test": "same", "passed": True, "runner": "docker-sandbox", "source_sha256": "c" * 64},
            {"test": "same", "passed": True, "runner": "docker-sandbox", "source_sha256": "c" * 64},
        ], _controlled_run=True,
    )
    result = learning_capabilities.get_capability_map(path_id=path_id)
    practical = next(row for row in result["capabilities"] if row["capability"] == "Not assessed")
    assert practical["status"] == "not_assessed"
    assert practical["practical_repeatable"] is False


def test_capability_map_reports_measured_model_tokens_and_inference_time(tmp_path, monkeypatch):
    path_id, topic_ids = _setup(tmp_path, monkeypatch)
    with learning_connection() as connection:
        job_id = connection.execute(
            "INSERT INTO learning_jobs(topic, status, source_kind, path_topic_id) VALUES ('Not assessed', 'completed', 'model_knowledge_no_external_source', ?)",
            (topic_ids[0],),
        ).lastrowid
    task = task_manager.create_task("learning.generate_lesson", {"job_id": int(job_id)})
    with learning_connection() as connection:
        connection.execute("UPDATE learning_jobs SET task_id=? WHERE id=?", (task["id"], job_id))
    with usage_metrics.capture_task(task["id"]):
        assert usage_metrics.record_ollama_response(
            purpose="learning", model="local-test-model",
            response={"prompt_eval_count": 120, "eval_count": 80, "total_duration": 2500000000,
                      "load_duration": 200000000, "prompt_eval_duration": 300000000, "eval_duration": 1000000000},
        ) is True

    result = learning_capabilities.get_capability_map(path_id=path_id)
    measured = next(row for row in result["capabilities"] if row["capability"] == "Not assessed")
    assert measured["model_call_count"] == 1
    assert measured["prompt_tokens"] == 120
    assert measured["completion_tokens"] == 80
    assert measured["inference_duration_ms"] == 2500.0
    assert measured["models_used"] == ["local-test-model"]
    assert measured["score_points_per_1000_output_tokens"] is None



def test_capability_map_requires_distinct_scenarios_linked_to_retained_knowledge_for_transfer(tmp_path, monkeypatch):
    path_id, topic_ids = _setup(tmp_path, monkeypatch)
    job = learning.create_job("Strong topic")
    with learning_connection() as connection:
        knowledge_id = int(connection.execute(
            """INSERT INTO knowledge_items(topic, content, source_kind, version, status, job_id)
               VALUES (?, ?, ?, 1, 'retained_after_review', ?)""",
            ("Strong topic", "Validated knowledge for transfer assessment.", "web_retrieved", job["id"]),
        ).lastrowid)

    sources = [{
        "url": "https://docs.python.org/3/library/",
        "claim": "Official reference excerpt",
        "excerpt": "A verified excerpt.",
        "live_content_sha256": "a" * 64,
        "excerpt_sha256": "b" * 64,
    }]
    for index, problem in enumerate((
        "Apply the retained concept to a cache invalidation scenario.",
        "Apply the same retained concept to a transaction visibility scenario.",
    ), start=1):
        item = experience.create_experience("Strong topic", problem, "Transfer hypothesis.")
        experience.update_experience(
            item["id"], status="verified", source_evidence=sources,
            test_plan=[{"test": f"transfer case {index}"}],
            test_results=[{
                "test": f"transfer case {index}", "passed": True, "runner": "docker-sandbox",
                "source_sha256": str(index) * 64,
                "source_evidence": [{
                    "url": sources[0]["url"],
                    "live_content_sha256": "a" * 64,
                    "excerpt_sha256": "b" * 64,
                }],
            }],
            _controlled_run=True,
        )
        with learning_connection() as connection:
            connection.execute(
                "UPDATE learning_experiences SET originating_knowledge_id=? WHERE id=?",
                (knowledge_id, item["id"]),
            )

    result = learning_capabilities.get_capability_map(path_id=path_id)
    transfer = next(row for row in result["capabilities"] if row["capability"] == "Strong topic")
    assert transfer["status"] == "transfer_provisional"
    assert transfer["transfer_repeatable"] is True
    assert transfer["transfer_case_count"] == 2
    assert knowledge_id in transfer["transfer_evidence_knowledge_ids"]
