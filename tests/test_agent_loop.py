import json

from app import agent_loop, db, experience, knowledge_base, ollama


def setup_learning_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db.defaults, "settings_db_path", str(tmp_path / "settings.db"))
    monkeypatch.setattr(db.defaults, "learning_db_path", str(tmp_path / "learning.db"))
    db.init_databases()
    experience.init_experience_tables()


def test_nontechnical_chat_skips_research_loop(monkeypatch):
    monkeypatch.setattr(ollama, "chat", lambda message, history=None: "Hello.")
    monkeypatch.setattr(agent_loop, "_expand_retrieval_query", lambda query: (_ for _ in ()).throw(AssertionError("should not expand")))
    assert agent_loop.answer_with_learning_loop("Hello") == "Hello."


def test_technical_chat_researches_revises_and_persists_verified_provenance(tmp_path, monkeypatch):
    setup_learning_db(tmp_path, monkeypatch)
    source_url = "https://docs.python.org/3/library/json.html"
    source_content = (
        "The json module provides functions for parsing JSON documents. "
        "Invalid JSON input may raise a decoding error. Applications should handle invalid external input. "
        "This exact excerpt is preserved for provenance checks and source attribution."
    )
    source = {
        "source_id": 1, "chunk_id": 1, "title": "Python JSON docs",
        "url": source_url, "content": source_content,
    }
    monkeypatch.setattr(ollama, "chat", lambda message, history=None: "Initial explanation without sources.")
    monkeypatch.setattr(agent_loop, "_expand_retrieval_query", lambda query: ["Python JSON decoding"])
    monkeypatch.setattr(knowledge_base, "search", lambda query, limit=5: [source])
    monkeypatch.setattr(knowledge_base, "research_and_index", lambda query, limit=3: {
        "query": query, "provider": "test", "allowed_hosts": [], "discovered_urls": [], "indexed": [], "errors": [],
    })

    revised = f"JSON decoding can fail on invalid input. Handle decoding errors at API boundaries. Reference: {source_url}"
    prompts = []

    def fake_request(_path, payload, **_kwargs):
        prompts.append(json.loads(payload["messages"][-1]["content"]))
        return {"message": {"content": revised}}

    monkeypatch.setattr(agent_loop, "_request", fake_request)
    answer = agent_loop.answer_with_learning_loop("Python JSON decoder throws an error")
    assert source_url in answer
    assert len(prompts) == 1
    records = experience.list_experiences(topic="Python JSON decoder")
    assert records[0]["status"] == "verified"
    assert records[0]["test_results"][0]["runner"] == "chat-learning-loop-v1"


def test_chat_reuses_verified_experience_on_later_technical_request(tmp_path, monkeypatch):
    setup_learning_db(tmp_path, monkeypatch)
    verified = experience.create_experience("Python JSON decoder", "A verified source-attributed explanation.")
    experience.update_experience(
        verified["id"],
        status="verified",
        source_evidence=[{
            "url": "https://docs.python.org/3/library/json.html",
            "claim": "Provenance only.",
            "excerpt": "The JSON module documentation excerpt.",
        }],
        test_plan=[{"test": "source_attribution"}],
        test_results=[{"test": "source_attribution", "passed": True}],
        takeaway="Handle expected JSON decoding errors explicitly.",
        _controlled_run=True,
    )
    monkeypatch.setattr(ollama, "chat", lambda message, history=None: "Initial draft.")
    monkeypatch.setattr(agent_loop, "_expand_retrieval_query", lambda query: ["Python JSON decoder"])
    monkeypatch.setattr(knowledge_base, "search", lambda query, limit=5: [])
    monkeypatch.setattr(knowledge_base, "research_and_index", lambda query, limit=3: {
        "query": query, "provider": "test", "allowed_hosts": [], "discovered_urls": [], "indexed": [], "errors": [],
    })
    captured = {}

    def fake_request(_path, payload, **_kwargs):
        captured["prompt"] = json.loads(payload["messages"][-1]["content"])
        return {"message": {"content": "Handle JSON decoding errors explicitly."}}

    monkeypatch.setattr(agent_loop, "_request", fake_request)
    answer = agent_loop.answer_with_learning_loop("How do I fix a Python JSON decoder error?")
    assert "Handle JSON" in answer
    assert "Verified prior experience" in captured["prompt"]["verified_prior_experiences"]



def test_static_syntax_failure_triggers_one_bounded_repair(tmp_path, monkeypatch):
    setup_learning_db(tmp_path, monkeypatch)
    fence = chr(96) * 3
    monkeypatch.setattr(ollama, "chat", lambda message, history=None: f"Example:\n{fence}python\nprint(\n{fence}")
    monkeypatch.setattr(agent_loop, "_expand_retrieval_query", lambda query: [])
    monkeypatch.setattr(knowledge_base, "search", lambda query, limit=5: [])
    monkeypatch.setattr(knowledge_base, "research_and_index", lambda query, limit=3: {
        "query": query, "provider": "test", "allowed_hosts": [], "discovered_urls": [], "indexed": [], "errors": [],
    })
    repair_calls = []

    def fake_request(_path, payload, **_kwargs):
        repair_calls.append(payload)
        return {"message": {"content": f'Example:\n{fence}python\nprint("ok")\n{fence}' }}

    monkeypatch.setattr(agent_loop, "_request", fake_request)
    answer = agent_loop.answer_with_learning_loop("Python code error: fix this syntax")
    assert 'print("ok")' in answer
    assert len(repair_calls) == 1
    assert all(item["passed"] for item in agent_loop._static_code_checks(answer))


def test_static_code_checks_detect_python_and_json_syntax_errors():
    fence = chr(96) * 3
    answer = f'{fence}python\nprint(\n{fence}\n{fence}json\n{{"key": }}\n{fence}'
    checks = agent_loop._static_code_checks(answer)
    assert len(checks) == 2
    assert all(item["passed"] is False for item in checks)
