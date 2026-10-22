import hashlib
import json
import time

from fastapi.testclient import TestClient

from app import claim_review, db, learning
from app.main import app


def test_learning_api_to_worker_persists_factual_claim_with_provenance(tmp_path, monkeypatch):
    monkeypatch.setattr(db.defaults, "settings_db_path", str(tmp_path / "settings.db"))
    monkeypatch.setattr(db.defaults, "learning_db_path", str(tmp_path / "learning.db"))
    monkeypatch.setattr(
        db.defaults, "cloud_resources_db_path", str(tmp_path / "cloud_resources.db")
    )
    monkeypatch.setattr(
        "app.cloud_resources.ensure_daily_discovery_scheduled",
        lambda: {"status": "disabled_for_test"},
    )

    url = "https://docs.python.org/3/library/stdtypes.html"
    excerpt = (
        "Python lists are mutable sequences. Their elements can be changed after "
        "creation, while indexed access preserves their defined order."
    )
    source_chunk = (
        f"Source title: Python sequence types\n"
        f"Source URL: {url}\n"
        f"Content:\n{excerpt}"
    )
    source_hash = hashlib.sha256(source_chunk.encode("utf-8")).hexdigest()

    def discover(job_id, _topic):
        with db.learning_connection() as conn:
            conn.execute(
                "INSERT INTO learning_source_text(job_id, content) VALUES (?, ?)",
                (job_id, source_chunk),
            )
            conn.execute(
                """INSERT INTO learning_evidence(
                       job_id, evidence_type, source_ref, content_hash, note
                   ) VALUES (?, 'web_page', ?, ?, ?)""",
                (job_id, url, source_hash, "Captured test fixture; not independently verified."),
            )
        return source_chunk, "web_retrieved"

    generated_lesson = {
        "lesson": (
            "Python lists are mutable ordered sequences. Each item has an index. "
            "A program can replace an item without creating a new list. This behavior "
            "is useful when a collection must be updated in place."
        ),
        "learning_outcomes": [
            "Describe list mutability",
            "Explain indexed access",
            "Identify an in-place update",
        ],
        "questions": [
            "What does mutable mean?",
            "How is an item selected?",
            "Can a list item be replaced?",
        ],
        "answer_key": [
            "Its items can be changed after creation.",
            "An index selects a position.",
            "Yes, an item can be assigned a new value.",
        ],
    }
    monkeypatch.setattr(learning, "_discover_and_store_sources", discover)
    monkeypatch.setattr(
        learning,
        "_request",
        lambda *_args, **_kwargs: {"message": {"content": json.dumps(generated_lesson)}},
    )
    monkeypatch.setattr(
        learning,
        "evaluate_learning_value",
        lambda *_args, **_kwargs: {
            "accepted": True,
            "score": 95,
            "reasons": [],
        },
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
                "reason": "The captured excerpt supports mutability.",
            }],
            "source_conflicts": [],
            "error": None,
            "reviewer": "local_ollama_model_assisted_not_independent",
        },
    )

    with TestClient(app, client=("127.0.0.1", 12345)) as client:
        response = client.post(
            "/api/learning/jobs",
            json={"topic": "Python list behavior"},
        )
        assert response.status_code == 202
        created = response.json()
        assert created["status"] in {"queued", "running", "completed"}
        assert isinstance(created.get("task_id"), int)

        deadline = time.monotonic() + 8.0
        task = None
        while time.monotonic() < deadline:
            task_response = client.get(f"/api/tasks/{created['task_id']}")
            assert task_response.status_code == 200
            task = task_response.json()
            if task["status"] in {"succeeded", "failed", "cancelled"}:
                break
            time.sleep(0.05)

        assert task is not None
        assert task["status"] == "succeeded"
        finished = client.get(f"/api/learning/jobs?limit=20")
        assert finished.status_code == 200
        job = next(item for item in finished.json()["jobs"] if item["id"] == created["id"])
        assert job["status"] == "completed"

        detail_response = client.get(f"/api/learning/jobs/{created['id']}")
        assert detail_response.status_code == 200
        detail = detail_response.json()
        assert detail["quality"]["accepted"] is True
        assert detail["quality"]["score"] == 95
        generation = detail["quality"]["generation_provenance"]
        assert generation["prompt_version"] == learning.LESSON_GENERATION_PROMPT_VERSION
        assert len(generation["prompt_sha256"]) == 64
        assert generation["repair_prompt_sha256"] is None
        assert generation["attempt_count"] == 1
        assert generation["model_id"]
        assert generation["model_digest"] is None
        assert len(detail["assessment"]["questions"]) == 3
        assert len(detail["assessment"]["learning_outcomes"]) == 3
        # The answer key remains hidden until an assessment attempt exists.
        assert "answer_key" not in detail["assessment"]
        evidence_types = {item["evidence_type"] for item in detail["evidence"]}
        assert {"web_page", "model_output"} <= evidence_types

        attempt_response = client.post(
            f"/api/learning/jobs/{created['id']}/assessment",
            json={"answers": generated_lesson["answer_key"]},
        )
        assert attempt_response.status_code == 200
        assert attempt_response.json()["score"] == 100
        refreshed = client.get(f"/api/learning/jobs/{created['id']}").json()
        assert refreshed["assessment_attempts"][0]["score"] == 100

        with db.learning_connection() as conn:
            stored_source = conn.execute(
                "SELECT content FROM learning_source_text WHERE job_id=?",
                (created["id"],),
            ).fetchone()
            claims = conn.execute(
                """SELECT id, claim, evidence_level, lifecycle_status
                   FROM factual_knowledge WHERE claim=?""",
                ("Python lists are mutable sequences.",),
            ).fetchall()
            evidence = conn.execute(
                """SELECT source_ref, source_hash, source_type, applicability_json
                   FROM factual_evidence WHERE knowledge_id=?""",
                (claims[0]["id"],),
            ).fetchall() if claims else []

        assert stored_source["content"] == source_chunk
        assert len(claims) == 1
        assert claims[0]["evidence_level"] == "L0_raw"
        assert claims[0]["lifecycle_status"] == "unverified"
        assert len(evidence) == 1
        assert evidence[0]["source_ref"] == url
        assert evidence[0]["source_hash"] == source_hash
        assert evidence[0]["source_type"] == "official_docs"
        applicability = json.loads(evidence[0]["applicability_json"])
        assert applicability["model_assisted_review_only"] is True
        assert applicability["independent_verification"] is False



def test_learning_repair_attempt_records_both_prompt_fingerprints(tmp_path, monkeypatch):
    monkeypatch.setattr(db.defaults, "settings_db_path", str(tmp_path / "settings.db"))
    monkeypatch.setattr(db.defaults, "learning_db_path", str(tmp_path / "learning.db"))
    db.init_databases()
    monkeypatch.setattr(
        learning, "detect_hardware",
        lambda: {"logical_cpu_count": 4, "total_ram_gb": 16, "available_ram_gb": 12},
    )
    generated = {
        "lesson": "Python decorators wrap callables and can add reusable behavior. A wrapper receives arguments, invokes the original function, and returns its result. The at-sign syntax applies the decorator when a function is defined. The functools.wraps helper preserves useful metadata. For example, a timing decorator can record how long a function takes. This is useful when the same behavior is needed across several functions.",
        "learning_outcomes": ["Describe a decorator", "Explain wrapper behavior", "Identify a use case"],
        "questions": ["What does a decorator wrap?", "What does a wrapper do?", "When is a decorator useful?"],
        "answer_key": ["A callable.", "It calls the original and returns the result.", "For reusable behavior."],
    }
    calls = []
    monkeypatch.setattr(
        learning, "_request",
        lambda _path, payload, **_kwargs: calls.append(payload) or {
            "message": {"content": json.dumps(generated, ensure_ascii=False)}
        },
    )
    quality_results = iter([
        {"accepted": False, "score": 40, "reasons": ["lesson too short"]},
        {"accepted": True, "score": 90, "reasons": []},
    ])
    monkeypatch.setattr(learning, "evaluate_learning_value", lambda *_args, **_kwargs: next(quality_results))
    job = learning.create_job("Python decorators", source_text="Official source excerpt about decorators.")
    learning.run_job(job["id"])
    finished = learning.get_job(job["id"])
    provenance = finished["quality"]["generation_provenance"]
    assert finished["status"] == "completed"
    assert len(calls) == 2
    assert provenance["prompt_version"] == learning.LESSON_GENERATION_PROMPT_VERSION
    assert len(provenance["prompt_sha256"]) == 64
    assert len(provenance["repair_prompt_sha256"]) == 64
    assert provenance["prompt_sha256"] != provenance["repair_prompt_sha256"]
    assert provenance["attempt_count"] == 2
