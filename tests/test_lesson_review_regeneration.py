import json

from app import db, experience, learning, knowledge_base


def test_regeneration_uses_only_review_for_same_topic_and_no_review_is_unchanged(tmp_path, monkeypatch):
    monkeypatch.setattr(db.defaults, "settings_db_path", str(tmp_path / "settings.db"))
    monkeypatch.setattr(db.defaults, "learning_db_path", str(tmp_path / "learning.db"))
    db.init_databases()

    with db.learning_connection() as conn:
        path_id = int(conn.execute(
            "INSERT INTO learning_paths(curriculum_slug, title) VALUES ('review-test', 'Review test')"
        ).lastrowid)
        topic_a_id = int(conn.execute(
            "INSERT INTO learning_path_topics(path_id, topic, position) VALUES (?, 'Topic A', 1)",
            (path_id,),
        ).lastrowid)
        topic_b_id = int(conn.execute(
            "INSERT INTO learning_path_topics(path_id, topic, position) VALUES (?, 'Topic B', 2)",
            (path_id,),
        ).lastrowid)

    previous = learning.create_job("Topic A", path_topic_id=topic_a_id)
    with db.learning_connection() as conn:
        conn.execute(
            "UPDATE learning_jobs SET status='completed', assessment_json=? WHERE id=?",
            (json.dumps({"learning_outcomes": ["one", "two", "three"]}), previous["id"]),
        )
    learning.submit_lesson_review(
        previous["id"],
        ["review", "partial", "mastered"],
        2,
        takeaway="The explanation needs a concrete example.",
        issues="The second paragraph is unclear.",
    )

    # Keep regeneration synchronous in the test; the worker is run explicitly below.
    monkeypatch.setattr(learning._learning_executor, "submit", lambda *_args, **_kwargs: None)
    regenerated_a = learning.regenerate_topic_learning(path_id, topic_a_id)
    regenerated_b = learning.regenerate_topic_learning(path_id, topic_b_id)

    captured_prompts = {}

    def fail_after_capture(_path, payload, **_kwargs):
        captured_prompts[payload["messages"][-1]["content"]] = True
        raise RuntimeError("Stop after capturing the generation prompt")

    monkeypatch.setattr(learning, "_expand_retrieval_query", lambda _topic: [])
    monkeypatch.setattr(knowledge_base, "search", lambda _query, limit=5: [])
    monkeypatch.setattr(knowledge_base, "research_and_index", lambda query, limit=3: {
        "query": query, "provider": "test", "allowed_hosts": [],
        "discovered_urls": [], "indexed": [], "errors": [],
    })
    monkeypatch.setattr(experience, "search_relevant_experiences", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(experience, "search_relevant_failures", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(learning, "_request", fail_after_capture)

    learning.run_job(regenerated_a["id"])
    learning.run_job(regenerated_b["id"])

    prompts = [json.loads(raw) for raw in captured_prompts]
    prompt_a = next(prompt for prompt in prompts if prompt["topic"] == "Topic A")
    prompt_b = next(prompt for prompt in prompts if prompt["topic"] == "Topic B")

    assert prompt_a["learner_review"]["value_rating"] == 2
    assert prompt_a["learner_review"]["takeaway"] == "The explanation needs a concrete example."
    assert prompt_a["learner_review"]["issues"] == "The second paragraph is unclear."
    assert "unverified quality signal" in prompt_a["review_guidance"]
    assert "learner_review" not in prompt_b
    assert "review_guidance" not in prompt_b

def test_regeneration_preserves_review_in_format_repair_prompt(tmp_path, monkeypatch):
    monkeypatch.setattr(db.defaults, "settings_db_path", str(tmp_path / "settings.db"))
    monkeypatch.setattr(db.defaults, "learning_db_path", str(tmp_path / "learning.db"))
    db.init_databases()

    with db.learning_connection() as conn:
        path_id = int(conn.execute(
            "INSERT INTO learning_paths(curriculum_slug, title) VALUES ('repair-review-test', 'Repair review test')"
        ).lastrowid)
        topic_id = int(conn.execute(
            "INSERT INTO learning_path_topics(path_id, topic, position) VALUES (?, 'Repair topic', 1)",
            (path_id,),
        ).lastrowid)

    previous = learning.create_job("Repair topic", path_topic_id=topic_id)
    with db.learning_connection() as conn:
        conn.execute(
            "UPDATE learning_jobs SET status='completed', assessment_json=? WHERE id=?",
            (json.dumps({"learning_outcomes": ["one", "two", "three"]}), previous["id"]),
        )
    learning.submit_lesson_review(
        previous["id"],
        ["review", "partial", "mastered"],
        2,
        takeaway="Add a concrete example.",
        issues="The explanation skips a key step.",
    )

    monkeypatch.setattr(learning._learning_executor, "submit", lambda *_args, **_kwargs: None)
    regenerated = learning.regenerate_topic_learning(path_id, topic_id)
    monkeypatch.setattr(learning, "_expand_retrieval_query", lambda _topic: [])
    monkeypatch.setattr(knowledge_base, "search", lambda _query, limit=5: [])
    monkeypatch.setattr(knowledge_base, "research_and_index", lambda query, limit=3: {
        "query": query, "provider": "test", "allowed_hosts": [],
        "discovered_urls": [], "indexed": [], "errors": [],
    })
    monkeypatch.setattr(experience, "search_relevant_experiences", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(experience, "search_relevant_failures", lambda *_args, **_kwargs: [])

    captured = []

    def invalid_json_response(_path, payload, **_kwargs):
        captured.append(json.loads(payload["messages"][-1]["content"]))
        return {"message": {"content": "not valid JSON"}}

    monkeypatch.setattr(learning, "_request", invalid_json_response)
    learning.run_job(regenerated["id"])

    assert len(captured) == 2
    repair_prompt = captured[1]
    assert repair_prompt["learner_review"]["takeaway"] == "Add a concrete example."
    assert repair_prompt["learner_review"]["issues"] == "The explanation skips a key step."
    assert "unverified quality signal" in repair_prompt["review_guidance"]
    assert "factual claims as evidence" in repair_prompt["review_guidance"]
