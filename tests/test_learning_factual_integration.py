import hashlib
import json

import pytest

from app import claim_review, db, learning


@pytest.fixture
def learning_store(tmp_path, monkeypatch):
    monkeypatch.setattr(db.defaults, "settings_db_path", str(tmp_path / "settings.db"))
    monkeypatch.setattr(db.defaults, "learning_db_path", str(tmp_path / "learning.db"))
    db.init_databases()


def test_python_lesson_review_persists_claims_as_unverified_factual_knowledge(
    learning_store, monkeypatch
):
    url = "https://docs.python.org/3/library/stdtypes.html"
    excerpt = (
        "Python lists are mutable sequences. Items can be changed after creation, "
        "and list order is preserved for indexed access."
    )
    source_chunk = f"Source title: Python sequence types\nSource URL: {url}\nContent:\n{excerpt}"
    source_hash = hashlib.sha256(source_chunk.encode("utf-8")).hexdigest()
    source_text = source_chunk

    def discover(_job_id, _topic):
        with db.learning_connection() as conn:
            conn.execute(
                """INSERT INTO learning_evidence(
                       job_id, evidence_type, source_ref, content_hash, note
                   ) VALUES (?, 'web_page', ?, ?, ?)""",
                (_job_id, url, source_hash, "Retrieved source; not independently verified."),
            )
        return source_text, "web_retrieved"

    lesson_result = {
        "lesson": "Python lists are mutable ordered sequences.",
        "learning_outcomes": ["Explain list mutability", "Read an indexed item", "Describe list ordering"],
        "questions": ["What is a list?"],
        "answer_key": ["A mutable ordered sequence."],
    }
    monkeypatch.setattr(learning, "_discover_and_store_sources", discover)
    monkeypatch.setattr(
        learning,
        "_request",
        lambda *_args, **_kwargs: {"message": {"content": json.dumps(lesson_result)}},
    )
    monkeypatch.setattr(
        learning,
        "evaluate_learning_value",
        lambda *_args, **_kwargs: {"accepted": True, "score": 95, "reasons": []},
    )
    monkeypatch.setattr(
        claim_review,
        "review_answer_claims",
        lambda *_args, **_kwargs: {
            "passed": True,
            "overall": "supported",
            "claims": [{
                "claim": "Python lists are mutable sequences.",
                "verdict": "supported",
                "evidence_urls": [url],
                "evidence_quotes": [{"url": url, "quote": excerpt}],
                "reason": "The source excerpt directly describes list mutability.",
            }],
            "source_conflicts": [],
            "error": None,
            "reviewer": "local_ollama_model_assisted_not_independent",
        },
    )

    job = learning.create_job("Python list behavior")
    learning.run_job(job["id"])
    finished = learning.get_job(job["id"])
    assert finished["status"] == "completed"

    with db.learning_connection() as conn:
        claims = conn.execute(
            "SELECT id, claim, evidence_level, lifecycle_status FROM factual_knowledge"
        ).fetchall()
        evidence = conn.execute(
            """SELECT source_type, source_ref, source_hash, excerpt, applicability_json
               FROM factual_evidence WHERE knowledge_id=?""",
            (claims[0]["id"],),
        ).fetchall()

    assert len(claims) == 1
    assert claims[0]["claim"] == "Python lists are mutable sequences."
    # A model-assisted pass is recorded as provenance, never as automatic promotion.
    assert claims[0]["evidence_level"] == "L0_raw"
    assert claims[0]["lifecycle_status"] == "unverified"
    assert len(evidence) == 1
    assert evidence[0]["source_type"] == "official_docs"
    assert evidence[0]["source_ref"] == url
    assert evidence[0]["source_hash"] == source_hash
    assert evidence[0]["excerpt"] == excerpt
    applicability = json.loads(evidence[0]["applicability_json"])
    assert applicability["model_assisted_review_only"] is True
    assert applicability["independent_verification"] is False


def test_repeated_factual_review_delivery_is_idempotent(learning_store):
    url = "https://docs.python.org/3/library/stdtypes.html"
    excerpt = (
        "Python dictionaries map unique keys to values. Keys are used to retrieve "
        "the corresponding values from a mapping."
    )
    source_chunk = f"Source title: Mapping types\nSource URL: {url}\nContent:\n{excerpt}"
    source_hash = hashlib.sha256(source_chunk.encode("utf-8")).hexdigest()
    review = {
        "reviewer": "local_ollama_model_assisted_not_independent",
        "claims": [{
            "claim": "Python dictionaries map keys to values.",
            "verdict": "supported",
            "evidence_urls": [url],
            "evidence_quotes": [{"url": url, "quote": excerpt}],
        }],
    }
    job = learning.create_job("Python dictionaries")
    with db.learning_connection() as conn:
        conn.execute(
            """INSERT INTO learning_evidence(
                   job_id, evidence_type, source_ref, content_hash, note
               ) VALUES (?, 'web_page', ?, ?, ?)""",
            (job["id"], url, source_hash, "Captured source; not independently verified."),
        )
        first_count = learning._persist_factual_review_claims(
            conn, job_id=job["id"], topic="Python dictionaries",
            source_text=source_chunk, review=review,
        )
        second_count = learning._persist_factual_review_claims(
            conn, job_id=job["id"], topic="Python dictionaries",
            source_text=source_chunk, review=review,
        )
        claim_rows = conn.execute(
            "SELECT id, evidence_level, lifecycle_status FROM factual_knowledge"
        ).fetchall()
        evidence_count = conn.execute(
            "SELECT COUNT(*) FROM factual_evidence"
        ).fetchone()[0]

    assert first_count == second_count == 1
    assert len(claim_rows) == 1
    assert claim_rows[0]["evidence_level"] == "L0_raw"
    assert claim_rows[0]["lifecycle_status"] == "unverified"
    assert evidence_count == 1


def test_review_cannot_attach_evidence_from_an_unmatched_url(learning_store):
    url = "https://docs.python.org/3/library/stdtypes.html"
    excerpt = (
        "Python tuples are immutable sequences. Their items cannot be replaced "
        "after the tuple has been created."
    )
    source_chunk = f"Source title: Sequence types\nSource URL: {url}\nContent:\n{excerpt}"
    source_hash = hashlib.sha256(source_chunk.encode("utf-8")).hexdigest()
    review = {
        "reviewer": "local_ollama_model_assisted_not_independent",
        "claims": [{
            "claim": "Python tuples are immutable sequences.",
            "verdict": "supported",
            "evidence_urls": ["https://example.invalid/fabricated-source"],
        }],
    }
    job = learning.create_job("Python tuples")
    with db.learning_connection() as conn:
        conn.execute(
            """INSERT INTO learning_evidence(
                   job_id, evidence_type, source_ref, content_hash, note
               ) VALUES (?, 'web_page', ?, ?, ?)""",
            (job["id"], url, source_hash, "Captured source; not independently verified."),
        )
        stored = learning._persist_factual_review_claims(
            conn, job_id=job["id"], topic="Python tuples",
            source_text=source_chunk, review=review,
        )
        claim = conn.execute(
            "SELECT evidence_level, lifecycle_status FROM factual_knowledge"
        ).fetchone()
        evidence_count = conn.execute(
            "SELECT COUNT(*) FROM factual_evidence"
        ).fetchone()[0]

    assert stored == 1
    assert claim["evidence_level"] == "L0_raw"
    assert claim["lifecycle_status"] == "unverified"
    assert evidence_count == 0


def test_factual_review_integration_persists_allowlisted_domains_without_promotion(learning_store):
    job = learning.create_job("Docker container networking")
    review = {
        "reviewer": "local_ollama_model_assisted_not_independent",
        "claims": [{
            "claim": "Docker bridge networks provide container connectivity.",
            "verdict": "supported",
            "evidence_urls": ["https://docs.docker.com/network/"],
        }],
    }
    with db.learning_connection() as conn:
        stored = learning._persist_factual_review_claims(
            conn,
            job_id=job["id"],
            topic="Docker container networking",
            source_text=(
                "Source title: Docker networking\n"
                "Source URL: https://docs.docker.com/network/\n"
                "Content:\nDocker bridge networks connect containers."
            ),
            review=review,
        )
        claim_count = conn.execute(
            "SELECT COUNT(*) FROM factual_knowledge"
        ).fetchone()[0]

    assert stored == 1
    assert claim_count == 1
    with db.learning_connection() as conn:
        claim = conn.execute(
            "SELECT environment_name, environment_version, evidence_level, lifecycle_status FROM factual_knowledge"
        ).fetchone()
    assert tuple(claim) == ("docker", "unspecified", "L0_raw", "unverified")


def test_learning_context_uses_only_exact_scope_policy_eligible_claims(learning_store):
    from app import factual_knowledge

    with db.learning_connection() as conn:
        conn.row_factory = __import__("sqlite3").Row
        knowledge_id = factual_knowledge.create_claim(
            conn,
            "SQL Server query plans expose selected execution operators",
            environment_name="sql_server",
            environment_version="unspecified",
            scope={"topic": "SQL Server query plans"},
        )
        factual_knowledge.add_evidence(
            conn,
            knowledge_id,
            source_type="official_docs",
            source_ref="https://learn.microsoft.com/sql/relational-databases/performance/execution-plans",
            source_hash="sha256:verified-source",
            excerpt="Execution plans show the operators selected by the optimizer.",
        )
        factual_knowledge.transition_evidence_level(conn, knowledge_id, "L1_sourced")
        factual_knowledge.record_test(
            conn,
            knowledge_id,
            test_kind="unit",
            test_name="sql-plan-scope",
            environment_name="sql_server",
            environment_version="unspecified",
            outcome="passed",
            result_summary="Applicable deterministic check passed.",
        )
        factual_knowledge.transition_evidence_level(conn, knowledge_id, "L2_tested")
        factual_knowledge.transition_lifecycle(conn, knowledge_id, "active")

    context = learning._verified_factual_context_for_topic("SQL Server query plans")
    assert len(context) == 1
    assert context[0]["claim_id"] == knowledge_id
    assert context[0]["status"] == "eligible_verified_factual_knowledge"
    assert context[0]["evidence_level"] == "L2_tested"
    assert context[0]["sources"][0]["url"].startswith("https://")
    assert context[0]["passing_tests"][0]["name"] == "sql-plan-scope"


def test_learning_context_fails_closed_for_ambiguous_topics_and_retrieval_errors(
    learning_store, monkeypatch
):
    from app import factual_knowledge

    assert learning._verified_factual_context_for_topic(
        "Python Docker deployment"
    ) == []

    def fail_retrieval(*_args, **_kwargs):
        raise RuntimeError("database details must not escape")

    monkeypatch.setattr(
        factual_knowledge, "search_retrievable_claims", fail_retrieval
    )
    assert learning._verified_factual_context_for_topic("SQL Server query plans") == []


def test_run_job_passes_verified_context_as_data_not_instructions(
    learning_store, monkeypatch
):
    captured = {}
    expected_context = [{
        "claim_id": 42,
        "claim": "SQL Server query plans expose operators",
        "evidence_level": "L2_tested",
        "domain": "sql_server",
        "environment": "sql_server unspecified",
        "sources": [{
            "url": "https://learn.microsoft.com/sql/execution-plans",
            "sha256": "sha256:source",
            "excerpt": "Official source excerpt",
        }],
        "passing_tests": [{
            "name": "scope-check",
            "environment": "sql_server unspecified",
            "result": "passed",
        }],
        "status": "eligible_verified_factual_knowledge",
    }]
    monkeypatch.setattr(
        learning, "_verified_factual_context_for_topic", lambda _topic: expected_context
    )
    lesson_result = {
        "lesson": "این یک درس فنی نمونه با توضیح کافی است.",
        "learning_outcomes": ["هدف اول", "هدف دوم", "هدف سوم"],
        "questions": ["سؤال نمونه"],
        "answer_key": ["پاسخ نمونه"],
    }

    def fake_request(_path, payload, **_kwargs):
        captured["prompt"] = json.loads(payload["messages"][1]["content"])
        return {"message": {"content": json.dumps(lesson_result, ensure_ascii=False)}}

    monkeypatch.setattr(learning, "_request", fake_request)
    monkeypatch.setattr(
        learning, "evaluate_learning_value",
        lambda *_args, **_kwargs: {"accepted": True, "score": 95, "reasons": []},
    )
    job = learning.create_job(
        "SQL Server query plans",
        source_text="User supplied lesson source text for a test.",
    )
    learning.run_job(job["id"])

    assert captured["prompt"]["verified_factual_context"] == expected_context
    policy = captured["prompt"]["verified_factual_context_policy"]
    assert "never as instructions" in policy
    assert "do not claim" in policy
    assert learning.get_job(job["id"])["status"] == "completed"


def test_user_source_is_bounded_for_model_context_but_preserved_in_storage(
    learning_store, monkeypatch
):
    source_text = ("Reference paragraph about safe SQL query design. " * 150)
    job = learning.create_job("General technical topic", source_text=source_text)
    # Simulate a settings change after the job was submitted but before a worker claims it.
    db.update_settings({"learning_source_max_chars": 1000})
    captured = {}

    monkeypatch.setattr(learning, "_verified_factual_context_for_topic", lambda _topic: [])
    monkeypatch.setattr(
        learning,
        "detect_hardware",
        lambda: {"logical_cpu_count": 4, "total_ram_gb": 16, "available_ram_gb": 12},
    )
    monkeypatch.setattr(
        learning,
        "runtime_limits",
        lambda _settings, _hardware: {"num_ctx": 2048, "cpu_threads": 2},
    )
    generated = {
        "lesson": "A short generated lesson used to verify the bounded context path.",
        "learning_outcomes": ["Describe the key design rule", "Explain its practical effect", "Identify a safe example"],
        "questions": ["What is the main rule?", "Why does it matter?", "How can it be checked?"],
        "answer_key": ["Use a clear condition.", "It reduces unintended behavior.", "Test the condition with representative input."],
    }

    def fake_request(_path, payload, timeout=None):
        captured["prompt"] = json.loads(payload["messages"][1]["content"])
        return {"message": {"content": json.dumps(generated)}}

    monkeypatch.setattr(learning, "_request", fake_request)
    monkeypatch.setattr(
        learning,
        "evaluate_learning_value",
        lambda *_args, **_kwargs: {"accepted": True, "score": 95, "reasons": []},
    )

    learning.run_job(job["id"])
    finished = learning.get_job(job["id"])
    with db.learning_connection() as conn:
        stored_source = conn.execute(
            "SELECT content FROM learning_source_text WHERE job_id=?", (job["id"],)
        ).fetchone()["content"]

    assert finished["status"] == "completed"
    assert len(captured["prompt"]["source_text"]) == 1000
    assert stored_source == source_text.strip()
    assert finished["quality"]["source_text_truncated"] is True
    assert finished["quality"]["source_characters_provided"] == len(stored_source)
    assert finished["quality"]["source_characters_sent_to_model"] == 1000



def test_factual_claim_persistence_does_not_attach_unmatched_source_quotes(learning_store):
    url = "https://docs.python.org/3/library/stdtypes.html"
    excerpt = "Python tuples are immutable sequences. Their items cannot be replaced after creation."
    source_chunk = f"Source title: Sequence types\\nSource URL: {url}\\nContent:\\n{excerpt}"
    source_hash = hashlib.sha256(source_chunk.encode("utf-8")).hexdigest()
    job = learning.create_job("Python tuple behavior")
    with db.learning_connection() as conn:
        conn.execute(
            """INSERT INTO learning_evidence(
                   job_id, evidence_type, source_ref, content_hash, note
               ) VALUES (?, 'web_page', ?, ?, ?)""",
            (job["id"], url, source_hash, "Captured source snapshot."),
        )
        stored = learning._persist_factual_review_claims(
            conn,
            job_id=job["id"],
            topic="Python tuple behavior",
            source_text=source_chunk,
            review={
                "reviewer": "model_assisted_test",
                "claims": [{
                    "claim": "Python tuples are immutable sequences.",
                    "verdict": "supported",
                    "evidence_urls": [url],
                    "evidence_quotes": [{
                        "url": url,
                        "quote": "This fabricated quote does not occur in the source snapshot.",
                    }],
                }],
            },
        )
        evidence_count = conn.execute("SELECT COUNT(*) FROM factual_evidence").fetchone()[0]
        claim = conn.execute(
            "SELECT evidence_level, lifecycle_status FROM factual_knowledge"
        ).fetchone()
    assert stored == 1
    assert evidence_count == 0
    assert tuple(claim) == ("L0_raw", "unverified")



@pytest.mark.parametrize(
    ("topic", "expected_domain", "expected_version"),
    [
        ("SQL Server 2022 execution plans", "sql_server", "2022"),
        ("Windows Server 2022 administration", "windows_server", "2022"),
        ("MikroTik RouterOS 7.15 routing", "mikrotik_routeros", "7.15"),
        ("VMware ESXi 8.0 storage", "vmware_esxi", "8.0"),
        ("MySQL 8.4 transaction isolation", "mysql", "8.4"),
        ("SQLite 3.46 locking", "sqlite", "3.46"),
        ("Python 3.12 async tasks", "python", "3.12"),
        ("Rust 1.82 ownership", "rust", "1.82"),
        ("ECMAScript 2024 modules", "javascript", "2024"),
        ("Docker container networking", "docker", "unspecified"),
        ("Linux Ubuntu service management", "linux", "unspecified"),
        ("Cisco IOS-XE routing", "cisco_networking", "unspecified"),
        ("Android application lifecycle", "android", "unspecified"),
        ("SwiftUI iOS application", "ios", "unspecified"),
        ("MetaTrader 5 MQL5 strategy", "forex_metatrader", "unspecified"),
    ],
)
def test_factual_topic_domain_coverage_is_explicit_and_conservative(
    learning_store, topic, expected_domain, expected_version
):
    domain = learning._factual_domain_for_topic(topic)
    assert domain is not None
    assert domain[0] == expected_domain
    assert domain[1] == expected_version


def test_factual_topic_domain_classifier_rejects_ambiguous_or_unrecognized_topics(
    learning_store,
):
    assert learning._factual_domain_for_topic("Python Docker deployment") is None
    assert learning._factual_domain_for_topic("general productivity advice") is None
