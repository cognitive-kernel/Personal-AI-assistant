import json

import pytest

from app import db, learning


def _make_review_fixture(tmp_path, monkeypatch):
    monkeypatch.setattr(db.defaults, "settings_db_path", str(tmp_path / "settings.db"))
    monkeypatch.setattr(db.defaults, "learning_db_path", str(tmp_path / "learning.db"))
    db.init_databases()

    with db.learning_connection() as conn:
        path_id = int(
            conn.execute(
                "INSERT INTO learning_paths(curriculum_slug, title) VALUES ('review-test', 'Review test')"
            ).lastrowid
        )
        topic_a_id = int(
            conn.execute(
                "INSERT INTO learning_path_topics(path_id, topic, position) VALUES (?, 'Topic A', 1)",
                (path_id,),
            ).lastrowid
        )
        topic_b_id = int(
            conn.execute(
                "INSERT INTO learning_path_topics(path_id, topic, position) VALUES (?, 'Topic B', 2)",
                (path_id,),
            ).lastrowid
        )

    previous = learning.create_job("Topic A", path_topic_id=topic_a_id)
    with db.learning_connection() as conn:
        conn.execute(
            "UPDATE learning_jobs SET status='completed', assessment_json=? WHERE id=?",
            (
                json.dumps(
                    {
                        "learning_outcomes": ["one", "two", "three"],
                        "questions": ["q1", "q2", "q3"],
                        "answer_key": ["a1", "a2", "a3"],
                    }
                ),
                previous["id"],
            ),
        )
    learning.submit_lesson_review(
        previous["id"],
        ["review", "partial", "mastered"],
        2,
        takeaway="The explanation needs a concrete example.",
        issues="The second paragraph is unclear.",
    )
    return path_id, topic_a_id, topic_b_id


def test_regeneration_uses_only_latest_review_for_same_topic(tmp_path, monkeypatch):
    path_id, topic_a_id, topic_b_id = _make_review_fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(learning, "_enqueue_learning_job", lambda _job_id: {})

    regenerated_a = learning.regenerate_topic_learning(path_id, topic_a_id)
    regenerated_b = learning.regenerate_topic_learning(path_id, topic_b_id)
    captured_prompts = []

    def capture_prompt_then_stop(_path, payload, **_kwargs):
        captured_prompts.append(json.loads(payload["messages"][-1]["content"]))
        raise RuntimeError("Stop after capturing the generation prompt")

    monkeypatch.setattr(learning, "_request", capture_prompt_then_stop)
    for job in (regenerated_a, regenerated_b):
        with pytest.raises(learning.LearningError, match="Learning job"):
            learning.run_job(job["id"])

    prompt_a = next(prompt for prompt in captured_prompts if prompt["topic"] == "Topic A")
    prompt_b = next(prompt for prompt in captured_prompts if prompt["topic"] == "Topic B")
    assert prompt_a["learner_review"]["value_rating"] == 2
    assert prompt_a["learner_review"]["takeaway"] == "The explanation needs a concrete example."
    assert prompt_a["learner_review"]["issues"] == "The second paragraph is unclear."
    assert "unverified quality signal" in prompt_a["review_guidance"]
    assert "learner_review" not in prompt_b
    assert "review_guidance" not in prompt_b


def test_review_context_is_bounded_and_is_not_copied_into_source_text(tmp_path, monkeypatch):
    path_id, topic_a_id, _topic_b_id = _make_review_fixture(tmp_path, monkeypatch)
    with db.learning_connection() as conn:
        conn.execute(
            "UPDATE lesson_reviews SET takeaway=?, issues=? WHERE job_id=(SELECT MIN(id) FROM learning_jobs WHERE path_topic_id=?)",
            ("T" * 3500, "I" * 3500, topic_a_id),
        )
    review = learning._latest_completed_topic_review(topic_a_id)
    assert review is not None
    assert len(review["takeaway"]) == 3000
    assert len(review["issues"]) == 3000

    monkeypatch.setattr(learning, "_enqueue_learning_job", lambda _job_id: {})
    job = learning.regenerate_topic_learning(path_id, topic_a_id)
    with db.learning_connection() as conn:
        stored = conn.execute(
            "SELECT lesson_review_json FROM learning_jobs WHERE id=?", (job["id"],)
        ).fetchone()[0]
        source = conn.execute(
            "SELECT content FROM learning_source_text WHERE job_id=?", (job["id"],)
        ).fetchone()
    assert json.loads(stored)["takeaway"] == "T" * 3000
    assert source is None



def test_regeneration_replaces_old_lesson_instead_of_keeping_duplicate_versions(
    tmp_path, monkeypatch
):
    path_id, topic_id, _topic_b_id = _make_review_fixture(tmp_path, monkeypatch)
    with db.learning_connection() as conn:
        previous = conn.execute(
            "SELECT id FROM learning_jobs WHERE path_topic_id=? ORDER BY id LIMIT 1",
            (topic_id,),
        ).fetchone()
        previous_id = int(previous["id"])
        conn.execute(
            "UPDATE learning_jobs SET lesson='Old lesson content' WHERE id=?",
            (previous_id,),
        )
        conn.execute(
            """INSERT INTO knowledge_items(topic, content, source_kind, version, status, job_id)
               VALUES ('Topic A', 'Old lesson content', 'model_knowledge_no_external_source', 1, 'unverified', ?)""",
            (previous_id,),
        )
    monkeypatch.setattr(learning, "_enqueue_learning_job", lambda _job_id: {})

    replacement = learning.regenerate_topic_learning(path_id, topic_id)

    with db.learning_connection() as conn:
        old_job = conn.execute(
            "SELECT status, lesson, assessment_json FROM learning_jobs WHERE id=?",
            (previous_id,),
        ).fetchone()
        old_knowledge_count = conn.execute(
            "SELECT COUNT(*) FROM knowledge_items WHERE job_id=?", (previous_id,)
        ).fetchone()[0]
        new_knowledge_count = conn.execute(
            "SELECT COUNT(*) FROM knowledge_items WHERE topic='Topic A'"
        ).fetchone()[0]

    assert replacement["id"] != previous_id
    assert old_job["status"] == "superseded"
    assert old_job["lesson"] is None
    assert old_job["assessment_json"] is None
    assert old_knowledge_count == 0
    assert new_knowledge_count == 0
