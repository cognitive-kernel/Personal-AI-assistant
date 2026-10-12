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
        system_prompt = payload["messages"][0]["content"]
        if "skeptical evidence reviewer" in system_prompt:
            return {"message": {"content": json.dumps({
                "overall": "supported",
                "claims": [{
                    "claim": "JSON decoding can fail on invalid input.",
                    "verdict": "supported",
                    "evidence_urls": [source_url],
                    "reason": "The supplied excerpt says invalid JSON may raise a decoding error.",
                }],
            })}}
        return {"message": {"content": revised}}

    monkeypatch.setattr(agent_loop, "_request", fake_request)
    answer = agent_loop.answer_with_learning_loop("Python JSON decoder throws an error")
    assert source_url in answer
    assert len(prompts) == 2
    records = experience.list_experiences(topic="Python JSON decoder")
    assert records[0]["status"] == "verified"
    semantic = next(item for item in records[0]["test_results"] if item["test"] == "semantic_claim_review")
    assert semantic["passed"] is True
    assert semantic["actual"]["reviewer"] == "local_ollama_model_assisted_not_independent"
    assert records[0]["test_results"][0]["runner"] == "chat-learning-loop-v1"
    evidence = records[0]["source_evidence"][0]
    import hashlib
    assert evidence["excerpt_sha256"] == hashlib.sha256(evidence["excerpt"].encode("utf-8")).hexdigest()
    assert evidence["original_language"] == "en"
    attribution = next(item for item in records[0]["test_results"] if item["test"] == "source_attribution")
    assert "research_metadata" in attribution


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



def test_failed_syntax_repair_is_persisted_when_research_finds_no_sources(tmp_path, monkeypatch):
    setup_learning_db(tmp_path, monkeypatch)
    fence = chr(96) * 3
    invalid = f"Example:\n{fence}python\nprint(\n{fence}"
    monkeypatch.setattr(ollama, "chat", lambda message, history=None: invalid)
    monkeypatch.setattr(agent_loop, "_expand_retrieval_query", lambda query: [])
    monkeypatch.setattr(knowledge_base, "search", lambda query, limit=5: [])
    monkeypatch.setattr(knowledge_base, "research_and_index", lambda query, limit=3: {
        "query": query, "provider": "test", "allowed_hosts": [], "discovered_urls": [], "indexed": [], "errors": [],
    })
    monkeypatch.setattr(
        agent_loop, "_request",
        lambda *_args, **_kwargs: {"message": {"content": invalid}},
    )

    answer = agent_loop.answer_with_learning_loop("Python code error: fix this syntax")
    assert answer == invalid
    records = experience.list_experiences(topic="Python code error")
    assert records[0]["status"] == "needs_review"
    syntax = [item for item in records[0]["test_results"] if item["test"] == "python_syntax_block_1"]
    assert syntax and syntax[0]["passed"] is False


def test_chat_verification_uses_cross_language_query_variants(tmp_path, monkeypatch):
    setup_learning_db(tmp_path, monkeypatch)
    source_url = "https://docs.python.org/3/library/json.html"
    source_content = (
        "The Python documentation describes JSON decoding behavior. "
        "Applications should handle malformed input and report decoding errors. "
        "This exact excerpt is retained for provenance and attribution verification."
    )
    result = agent_loop._remember_chat_result(
        query="خطای رمزگشایی JSON در پایتون",
        query_variants=["Python JSON decoding error"],
        answer=f"Handle malformed JSON and decoding errors. Source: {source_url}",
        sources=[{
            "source_id": 1, "chunk_id": 1, "title": "Python JSON docs",
            "url": source_url, "content": source_content,
        }],
        prior_experience_ids=[],
        research_metadata={"attempts": []},
    )
    assert result["status"] == "needs_review"
    relevance = next(check for check in result["checks"] if check["test"] == "source_relevance")
    assert relevance["passed"] is True
    semantic = next(check for check in result["checks"] if check["test"] == "semantic_claim_review")
    assert semantic["passed"] is False


def test_legacy_chat_experience_without_claim_review_is_not_reused(tmp_path, monkeypatch):
    setup_learning_db(tmp_path, monkeypatch)
    legacy = experience.create_experience(
        "Python JSON decoder",
        "Legacy chat answer with provenance checks only.",
    )
    experience.update_experience(
        legacy["id"],
        status="verified",
        source_evidence=[{
            "url": "https://docs.python.org/3/library/json.html",
            "claim": "Provenance only.",
            "excerpt": "The JSON module documentation excerpt.",
        }],
        test_plan=[{"test": "source_attribution", "runner": "chat-learning-loop-v1"}],
        test_results=[{
            "test": "source_attribution",
            "passed": True,
            "runner": "chat-learning-loop-v1",
        }],
        takeaway="A legacy answer that has not passed claim-level review.",
        _controlled_run=True,
    )
    assert experience.search_relevant_experiences(
        "Python JSON decoder",
        ["Python JSON decoder"],
    ) == []
