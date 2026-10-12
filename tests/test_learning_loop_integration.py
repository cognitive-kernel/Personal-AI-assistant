import json

from app import db, experience, learning, knowledge_base


def test_failed_learning_attempt_is_retried_persisted_and_retrieved_later(tmp_path, monkeypatch):
    monkeypatch.setattr(db.defaults, "settings_db_path", str(tmp_path / "settings.db"))
    monkeypatch.setattr(db.defaults, "learning_db_path", str(tmp_path / "learning.db"))
    db.init_databases()
    experience.init_experience_tables()

    bad_artifact = {
        "lesson": "Bad.",
        "learning_outcomes": ["Outcome one", "Outcome two", "Outcome three"],
        "questions": ["Question one?", "Question two?", "Question three?"],
        "answer_key": ["Answer one.", "Answer two.", "Answer three."],
    }
    calls = []

    def fake_request(_path, payload, **_kwargs):
        calls.append(payload)
        user_content = payload["messages"][-1]["content"]
        if "search strings" in payload["messages"][0]["content"]:
            return {"message": {"content": json.dumps({"variants": ["SQL Server query plan"]})}}
        return {"message": {"content": json.dumps(bad_artifact, ensure_ascii=False)}}

    monkeypatch.setattr(learning, "_request", fake_request)
    monkeypatch.setattr(knowledge_base, "search", lambda query, limit=5: [])
    monkeypatch.setattr(knowledge_base, "research_and_index", lambda query, limit=3: {
        "query": query, "provider": "test", "allowed_hosts": [], "discovered_urls": [], "indexed": [], "errors": []
    })

    job = learning.create_job("SQL Server query plan")
    learning.run_job(job["id"])

    finished = learning.get_job(job["id"])
    assert finished["status"] == "rejected"
    assert len([call for call in calls if call["options"].get("num_predict", 0) >= 1000]) == 2

    failures = experience.search_relevant_failures(
        "SQL Server query plan",
        query_variants=["execution plan"],
    )
    assert len(failures) == 1
    assert failures[0]["status"] == "needs_review"
    assert "quality gate" in failures[0]["problem"].lower()
    assert experience.search_relevant_experiences("SQL Server query plan") == []
