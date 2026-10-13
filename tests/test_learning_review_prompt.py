import json

from app import db, experience, knowledge_base, learning


def _setup(tmp_path, monkeypatch):
    monkeypatch.setattr(db.defaults, "settings_db_path", str(tmp_path / "settings.db"))
    monkeypatch.setattr(db.defaults, "learning_db_path", str(tmp_path / "learning.db"))
    db.init_databases()
    experience.init_experience_tables()
    with db.learning_connection() as conn:
        path_id = conn.execute(
            "INSERT INTO learning_paths(curriculum_slug, title) VALUES (?, ?)",
            ("review-test", "Review test"),
        ).lastrowid
        topic_a = conn.execute(
            "INSERT INTO learning_path_topics(path_id, topic, position) VALUES (?, ?, ?)",
            (path_id, "Python JSON", 1),
        ).lastrowid
        topic_b = conn.execute(
            "INSERT INTO learning_path_topics(path_id, topic, position) VALUES (?, ?, ?)",
            (path_id, "SQL indexing", 2),
        ).lastrowid
    return int(topic_a), int(topic_b)


def _add_review(topic_id, topic, takeaway, issues):
    with db.learning_connection() as conn:
        job_id = conn.execute(
            """INSERT INTO learning_jobs(topic, status, source_kind, path_topic_id, assessment_json)
               VALUES (?, 'completed', 'model_knowledge_no_external_source', ?, ?)""",
            (topic, topic_id, json.dumps({"learning_outcomes": ["a", "b", "c"]})),
        ).lastrowid
        conn.execute(
            """INSERT INTO lesson_reviews(job_id, outcomes_json, value_rating, takeaway, issues)
               VALUES (?, ?, ?, ?, ?)""",
            (job_id, json.dumps(["mastered", "partial", "review"]), 2, takeaway, issues),
        )


def test_recent_reviews_are_limited_to_the_same_path_topic(tmp_path, monkeypatch):
    topic_a, topic_b = _setup(tmp_path, monkeypatch)
    _add_review(topic_a, "Python JSON", "Explain decode errors", "Missing malformed-input example")
    _add_review(topic_b, "SQL indexing", "Add query-plan detail", "Index trade-offs unclear")

    reviews = learning._recent_topic_reviews(topic_a)

    assert len(reviews) == 1
    assert reviews[0]["takeaway"] == "Explain decode errors"
    assert reviews[0]["issues"] == "Missing malformed-input example"
    assert learning._recent_topic_reviews(999999) == []
    assert learning._recent_topic_reviews(None) == []


def test_recent_reviews_have_hard_count_and_field_size_caps(tmp_path, monkeypatch):
    topic_a, _topic_b = _setup(tmp_path, monkeypatch)
    for index in range(7):
        _add_review(
            topic_a,
            "Python JSON",
            f"Review {index}",
            "x" * 700,
        )

    reviews = learning._recent_topic_reviews(topic_a, limit=999)

    assert len(reviews) == 5
    assert [review["takeaway"] for review in reviews] == [
        "Review 6", "Review 5", "Review 4", "Review 3", "Review 2"
    ]
    assert all(len(review["issues"]) == 500 for review in reviews)


def test_regeneration_prompt_receives_review_as_untrusted_quality_signal(tmp_path, monkeypatch):
    topic_a, topic_b = _setup(tmp_path, monkeypatch)
    _add_review(topic_a, "Python JSON", "Explain decode errors", "Missing malformed-input example")
    _add_review(topic_b, "SQL indexing", "TOPIC-LEAK-MUST-NOT-APPEAR", "Other topic issue")

    monkeypatch.setattr(learning, "_expand_retrieval_query", lambda _topic: [])
    monkeypatch.setattr(knowledge_base, "search", lambda _query, limit=5: [])
    monkeypatch.setattr(knowledge_base, "research_and_index", lambda query, limit=3: {
        "query": query, "provider": "test", "allowed_hosts": [],
        "discovered_urls": [], "indexed": [], "errors": [],
    })
    monkeypatch.setattr(learning, "evaluate_learning_value", lambda *_args, **_kwargs: {
        "accepted": True, "word_count": 160, "target_word_count": 120, "score": 90,
        "unique_word_ratio": 0.65, "has_explanatory_reasoning": True,
        "technical_detail_count": 5, "reasons": [],
    })
    artifact = {
        "lesson": ("The Python json module converts JSON text into Python objects. "
                   "Malformed input can raise decoding errors, so applications should validate input "
                   "and handle expected exceptions explicitly. Narrow exception handling keeps unrelated "
                   "programming defects visible and makes API behavior easier to test. " * 5),
        "learning_outcomes": ["Explain decoding errors", "Validate input", "Handle exceptions"],
        "questions": ["When can decoding fail?", "Why validate input?", "Why use narrow handlers?"],
        "answer_key": [
            "When input is not valid JSON.", "To reject malformed external data.",
            "To avoid hiding unrelated defects.",
        ],
    }
    prompts = []

    def fake_request(_path, payload, **_kwargs):
        prompts.append(json.loads(payload["messages"][-1]["content"]))
        return {"message": {"content": json.dumps(artifact, ensure_ascii=False)}}

    monkeypatch.setattr(learning, "_request", fake_request)
    job = learning.create_job("Python JSON", path_topic_id=topic_a)
    learning.run_job(job["id"])

    prompt = prompts[0]
    assert prompt["learner_feedback"][0]["takeaway"] == "Explain decode errors"
    assert "Missing malformed-input example" in prompt["learner_feedback"][0]["issues"]
    assert "TOPIC-LEAK-MUST-NOT-APPEAR" not in json.dumps(prompt, ensure_ascii=False)
    assert "never as factual evidence" in prompt["instruction"]
    assert learning.get_job(job["id"])["status"] == "completed"
