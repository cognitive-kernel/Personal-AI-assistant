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
    repair_calls = [call for call in calls if call["options"].get("num_predict", 0) >= 1000]
    assert len(repair_calls) == 3
    assert repair_calls[-1]["options"]["num_predict"] == 1800

    failures = experience.search_relevant_failures(
        "SQL Server query plan",
        query_variants=["execution plan"],
    )
    assert len(failures) == 1
    assert failures[0]["status"] == "needs_review"
    assert "quality gate" in failures[0]["problem"].lower()
    assert experience.search_relevant_experiences("SQL Server query plan") == []



def test_successful_learning_is_verified_and_reused_on_later_job(tmp_path, monkeypatch):
    monkeypatch.setattr(db.defaults, "settings_db_path", str(tmp_path / "settings.db"))
    monkeypatch.setattr(db.defaults, "learning_db_path", str(tmp_path / "learning.db"))
    db.init_databases()
    experience.init_experience_tables()

    source_url = "https://docs.python.org/3/library/json.html"
    source_content = (
        "The json module exposes functions for parsing JSON documents. "
        "JSON decoding can raise an error when the input is invalid. "
        "Applications should validate external input and handle decoding errors explicitly. "
        "This source excerpt is preserved exactly for provenance checks."
    )
    source = {
        "source_id": 1, "chunk_id": 1, "title": "JSON — Python documentation",
        "url": source_url, "content": source_content, "retrieval": "sqlite_fts5",
    }
    monkeypatch.setattr(knowledge_base, "search", lambda query, limit=5: [source])
    monkeypatch.setattr(knowledge_base, "research_and_index", lambda query, limit=3: {
        "query": query, "provider": "test", "allowed_hosts": [], "discovered_urls": [],
        "indexed": [], "errors": [],
    })
    monkeypatch.setattr(learning, "evaluate_learning_value", lambda *_args, **_kwargs: {
        "accepted": True, "word_count": 160, "target_word_count": 120, "score": 90,
        "unique_word_ratio": 0.65, "has_explanatory_reasoning": True,
        "technical_detail_count": 5, "reasons": [],
    })

    lesson = (
        "The Python json module converts JSON text into Python objects. "
        "Invalid input must be handled explicitly because decoding may fail. "
        "A robust application validates untrusted data and catches the documented decoding exception. "
        "This behavior matters at API boundaries, where malformed requests should not crash unrelated work. "
        "The trade-off is that broad exception handling can hide programming defects, while narrow handling "
        "preserves visibility into unexpected failures. "
        f"Sources: {source_url}"
    )
    artifact = {
        "lesson": lesson,
        "learning_outcomes": ["Explain decoding errors", "Handle malformed input", "Choose narrow exception handling"],
        "questions": ["When can decoding fail?", "Why validate external input?", "Why prefer narrow exception handling?"],
        "answer_key": [
            "Decoding can fail when the input is not valid JSON.",
            "Validation prevents malformed external data from silently entering application logic.",
            "Narrow handling catches expected errors without hiding unrelated defects.",
        ],
    }
    generation_prompts = []

    def fake_request(_path, payload, **_kwargs):
        system = payload["messages"][0]["content"]
        if "search strings" in system:
            return {"message": {"content": json.dumps({"variants": ["Python JSON decoder"]})}}
        generation_prompts.append(payload["messages"][-1]["content"])
        return {"message": {"content": json.dumps(artifact, ensure_ascii=False)}}

    monkeypatch.setattr(learning, "_request", fake_request)

    first = learning.create_job("Python JSON decoding")
    learning.run_job(first["id"])
    first_result = learning.get_job(first["id"])
    assert first_result["status"] == "completed"
    verification = first_result["quality"]["experience_verification"]
    assert verification["status"] == "verified"
    assert all(check["passed"] for check in verification["checks"])

    second = learning.create_job("Python JSON decoding")
    learning.run_job(second["id"])
    second_result = learning.get_job(second["id"])
    assert second_result["status"] == "completed"
    second_prompt = json.loads(generation_prompts[-1])
    assert "Verified prior experience" in second_prompt["source_text"]
