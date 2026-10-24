import hashlib
import json

import pytest
from fastapi.testclient import TestClient

from app import db, learning, learning_review_channels as review, task_manager, sandbox, sandbox_consent
from app.learning_review_tasks import handle_candidate_review
from app.main import app
from app.task_worker import TaskContext


@pytest.fixture
def review_store(tmp_path, monkeypatch):
    monkeypatch.setattr(db.defaults, "settings_db_path", str(tmp_path / "settings.db"))
    monkeypatch.setattr(db.defaults, "learning_db_path", str(tmp_path / "learning.db"))
    db.init_databases()
    return TestClient(app, client=("127.0.0.1", 12345))


def _candidate(*, topic="Python candidate review", content="A temporary lesson with a specific testable claim."):
    job = learning.create_job(topic, source_text="Original user source with enough text for a stored evidence context.")
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    with db.learning_connection() as connection:
        cursor = connection.execute(
            """INSERT INTO temporary_knowledge_candidates(
                   job_id, topic, content, source_kind, source_urls_json,
                   content_sha256, quality_report_json
               ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (job["id"], topic, content, "user_provided_text",
             json.dumps(["https://docs.python.org/3/"]), digest, json.dumps({"accepted": True})),
        )
        return int(cursor.lastrowid), job["id"]


def _test_plan_hash(candidate_id: int) -> str:
    with db.learning_connection() as connection:
        raw = connection.execute(
            "SELECT test_plan_json FROM temporary_knowledge_candidates WHERE id=?", (candidate_id,)
        ).fetchone()["test_plan_json"]
    plan = json.loads(raw)
    canonical = json.dumps(plan, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def test_web_research_persists_context_without_promoting_candidate(review_store, monkeypatch):
    from app import web_sources
    candidate_id, _job_id = _candidate()
    monkeypatch.setattr(
        web_sources,
        "discover_sources",
        lambda query, limit: [
            web_sources.SourceDocument(
                url="https://docs.python.org/3/library/stdtypes.html",
                title="Python built-in types",
                text="A bounded source excerpt that gives context for the candidate lesson and its conditions.",
            )
        ],
    )

    result = review.research_candidate_online(candidate_id, "Python built-in types", limit=1)

    assert result["status"] == "completed"
    assert result["stored"] == 1
    with db.learning_connection() as connection:
        candidate = connection.execute(
            "SELECT status, trust_level, learning_status FROM temporary_knowledge_candidates WHERE id=?",
            (candidate_id,),
        ).fetchone()
        evidence = connection.execute(
            "SELECT evidence_kind, source_url, source_sha256 FROM knowledge_review_evidence WHERE candidate_id=?",
            (candidate_id,),
        ).fetchone()
    assert tuple(candidate) == ("awaiting_test", "L0", "unverified")
    assert evidence["evidence_kind"] == "context"
    assert evidence["source_url"].startswith("https://")
    assert len(evidence["source_sha256"]) == 64


def test_missing_peer_configuration_is_recorded_without_failure_or_promotion(review_store, monkeypatch):
    candidate_id, _job_id = _candidate()
    monkeypatch.delenv("PEER_AI_PROVIDERS_JSON", raising=False)

    result = review.consult_configured_peer_ais(candidate_id)

    assert result["status"] == "unconfigured"
    assert result["consulted"] == 0
    with db.learning_connection() as connection:
        attempt = connection.execute(
            "SELECT channel, status FROM knowledge_review_attempts WHERE candidate_id=? ORDER BY id DESC LIMIT 1",
            (candidate_id,),
        ).fetchone()
        candidate = connection.execute(
            "SELECT trust_level, learning_status, status FROM temporary_knowledge_candidates WHERE id=?",
            (candidate_id,),
        ).fetchone()
    assert tuple(attempt) == ("peer_ai", "unconfigured")
    assert tuple(candidate) == ("L0", "unverified", "awaiting_test")


def test_configured_peer_response_is_audited_but_not_treated_as_truth(review_store, monkeypatch):
    candidate_id, _job_id = _candidate()
    monkeypatch.setenv("PEER_AI_PROVIDERS_JSON", json.dumps([{
        "provider": "local-test-provider",
        "base_url": "http://127.0.0.1:8000/v1",
        "model": "review-model-a",
    }]))
    answer = json.dumps({
        "stance": "supports",
        "claims": [{"claim": "Example claim", "confidence": 0.7}],
        "citations": [],
        "limitations": ["This is model opinion only."],
        "applications": [],
        "testable_tests": [],
    })
    body = json.dumps({"choices": [{"message": {"content": answer}}]}).encode("utf-8")

    class Response:
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def read(self, size): return body

    captured = {}
    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["timeout"] = timeout
        return Response()

    monkeypatch.setattr(review.urllib.request, "urlopen", fake_urlopen)
    result = review.consult_configured_peer_ais(candidate_id)

    assert result["status"] == "completed"
    assert result["consulted"] == 1
    assert captured["url"] == "http://127.0.0.1:8000/v1/chat/completions"
    assert captured["timeout"] == 15
    with db.learning_connection() as connection:
        peer = connection.execute(
            """SELECT provider, model_name, prompt_version, stance, response_sha256
               FROM knowledge_peer_consultations WHERE candidate_id=?""",
            (candidate_id,),
        ).fetchone()
        candidate = connection.execute(
            "SELECT trust_level, learning_status, status FROM temporary_knowledge_candidates WHERE id=?",
            (candidate_id,),
        ).fetchone()
    assert peer["provider"] == "local-test-provider"
    assert peer["model_name"] == "review-model-a"
    assert peer["prompt_version"] == review.PEER_REVIEW_PROMPT_VERSION
    assert peer["stance"] == "supports"
    assert len(peer["response_sha256"]) == 64
    assert tuple(candidate) == ("L0", "unverified", "awaiting_test")


def test_peer_provider_rejects_plain_http_non_loopback(review_store, monkeypatch):
    candidate_id, _job_id = _candidate()
    monkeypatch.setenv("PEER_AI_PROVIDERS_JSON", json.dumps([{
        "provider": "unsafe-provider",
        "base_url": "http://example.com/v1",
        "model": "review-model",
    }]))
    monkeypatch.setattr(
        review.urllib.request, "urlopen",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("network call must not happen")),
    )

    result = review.consult_configured_peer_ais(candidate_id)

    assert result["status"] == "unavailable"
    assert result["consulted"] == 0
    assert result["errors"][0]["error"] == "endpoint_must_be_https_or_loopback_without_credentials"


def test_candidate_review_queue_is_idempotent_and_hash_gated(review_store):
    candidate_id, _job_id = _candidate()
    request = {
        "confirm": True,
        "request_id": "d25e9f80-7c5e-4ed0-9a55-4f12718b5f89",
        "query": "Python candidate review",
        "web_mode": "disabled",
    }
    first = review_store.post(f"/api/learning/candidates/{candidate_id}/review", json=request)
    second = review_store.post(f"/api/learning/candidates/{candidate_id}/review", json=request)
    assert first.status_code == 202
    assert second.status_code == 202
    assert first.json()["task_id"] == second.json()["task_id"]
    task = task_manager.get_task(first.json()["task_id"])
    assert task["task_type"] == "learning.review_candidate"
    assert task["payload"] == {
        "candidate_id": candidate_id,
        "query": "Python candidate review",
        "web_mode": "disabled",
    }

    with db.learning_connection() as connection:
        connection.execute(
            "UPDATE temporary_knowledge_candidates SET content='tampered' WHERE id=?",
            (candidate_id,),
        )
    blocked = review_store.post(
        f"/api/learning/candidates/{candidate_id}/review",
        json={**request, "request_id": "8f9dba44-2a10-4b20-bbcb-7a0fbe2fda15"},
    )
    assert blocked.status_code == 409


def test_candidate_review_history_returns_audit_and_never_claims_promotion(review_store):
    candidate_id, _job_id = _candidate()
    review.record_review_attempt(
        candidate_id, channel="web_research", status="unconfigured",
        details={"reason": "test"},
    )
    review.record_review_evidence(
        candidate_id,
        evidence_kind="context",
        source_url="https://docs.python.org/3/",
        source_title="Python docs",
        excerpt="A source excerpt used for context; support has not been independently adjudicated.",
        reliability_note="Fetched for review only.",
    )

    response = review_store.get(f"/api/learning/candidates/{candidate_id}/review-history")

    assert response.status_code == 200
    result = response.json()
    assert result["promoted"] is False
    assert result["attempts"][0]["channel"] == "web_research"
    assert result["evidence"][0]["evidence_kind"] == "context"
    with db.learning_connection() as connection:
        candidate = connection.execute(
            "SELECT trust_level, learning_status, status FROM temporary_knowledge_candidates WHERE id=?",
            (candidate_id,),
        ).fetchone()
    assert tuple(candidate) == ("L0", "unverified", "awaiting_test")


def test_candidate_review_worker_runs_channels_independently_and_never_promotes(review_store, monkeypatch):
    candidate_id, job_id = _candidate()
    checkpoints = []
    class Context:
        task_id = 101
        def checkpoint(self, value): checkpoints.append(value)

    monkeypatch.setattr(review, "research_candidate_online", lambda *args, **kwargs: {"status": "unavailable"})
    monkeypatch.setattr(review, "consult_configured_peer_ais", lambda *args, **kwargs: {"status": "unconfigured"})
    result = handle_candidate_review(
        {"candidate_id": candidate_id, "query": "Python candidate review", "web_mode": "search"},
        None,
        Context(),
    )

    assert result["channels"]["web_research"]["status"] == "unavailable"
    assert result["channels"]["peer_ai"]["status"] == "unconfigured"
    assert result["promoted"] is False
    assert [item["phase"] for item in checkpoints] == [
        "candidate_review_started", "candidate_review_peer_ai", "candidate_review_completed"
    ]
    from app import learning_candidate_channels
    channel_runs = learning_candidate_channels.list_candidate_channel_runs(candidate_id)
    assert {item["channel"]: item["status"] for item in channel_runs} == {
        "research_web": "skipped", "peer_ai": "skipped"
    }
    with db.learning_connection() as connection:
        candidate = connection.execute(
            "SELECT trust_level, learning_status, status FROM temporary_knowledge_candidates WHERE id=?",
            (candidate_id,),
        ).fetchone()
    assert tuple(candidate) == ("L0", "unverified", "awaiting_test")

    from app.learning_durable_budget import get_job_budget
    first_budget = get_job_budget(job_id)
    assert first_budget["calls_used"] == 4
    assert first_budget["predict_tokens_reserved"] == 6300
    repeated = handle_candidate_review(
        {"candidate_id": candidate_id, "query": "Python candidate review", "web_mode": "search"},
        None,
        Context(),
    )
    assert repeated["channels"] == result["channels"]
    assert get_job_budget(job_id)["calls_used"] == first_budget["calls_used"]
    assert get_job_budget(job_id)["predict_tokens_reserved"] == first_budget["predict_tokens_reserved"]


def test_shared_pipeline_budget_blocks_review_actions_when_job_budget_is_exhausted(review_store, monkeypatch):
    from app.learning_durable_budget import reserve_job_budget
    candidate_id, job_id = _candidate()
    reserve_job_budget(job_id, "preexisting_budget_usage", calls=7, predict_tokens=1)
    called = []
    monkeypatch.setattr(review, "research_candidate_online", lambda *args, **kwargs: called.append("web") or {"status": "completed"})
    monkeypatch.setattr(review, "consult_configured_peer_ais", lambda *args, **kwargs: called.append("peer") or {"status": "completed"})

    class Context:
        task_id = 808
        def checkpoint(self, value): pass

    result = handle_candidate_review(
        {"candidate_id": candidate_id, "query": "Python candidate review", "web_mode": "search"},
        None,
        Context(),
    )

    assert result["channels"]["web_research"]["reason"] == "shared_pipeline_budget_exhausted"
    assert result["channels"]["peer_ai"]["reason"] == "shared_pipeline_budget_exhausted"
    assert called == []
    from app.learning_durable_budget import get_job_budget
    assert get_job_budget(job_id)["calls_used"] == 7


def test_candidate_sandbox_review_requires_consent_configuration_and_reviewed_plan(review_store, monkeypatch, tmp_path):
    candidate_id, _job_id = _candidate(content="A candidate example.\n\n```python\nprint('safe example')\n```")
    request = {"confirm": True, "request_id": "8a1f3e2a-1f3d-4b7e-9d11-2d2e2fba7c88"}
    path = f"/api/learning/candidates/{candidate_id}/sandbox-test"

    denied = review_store.post(path, json=request)
    assert denied.status_code == 409

    with db.learning_connection() as connection:
        connection.execute(
            "UPDATE temporary_knowledge_candidates SET test_plan_json=? WHERE id=?",
            (json.dumps({"version": 2, "oracle_reviewed": True, "tests": [{"name": "reviewed", "python_test_code": "print('safe example')", "expected_stdout": "ok"}]}), candidate_id),
        )
    docker = tmp_path / "docker"
    docker.write_text("test executable", encoding="utf-8")
    monkeypatch.setenv("SANDBOX_ENABLED", "true")
    monkeypatch.setenv("SANDBOX_DOCKER_EXECUTABLE", str(docker))
    monkeypatch.setenv("SANDBOX_PYTHON_IMAGE", "python:3.12-slim@sha256:" + "a" * 64)
    sandbox_consent.grant_consent()

    queued = review_store.post(path, json=request)
    assert queued.status_code == 202
    task = task_manager.get_task(queued.json()["task_id"])
    assert task["task_type"] == "learning.sandbox_candidate"
    assert task["payload"]["content_sha256"] == hashlib.sha256(
        "A candidate example.\n\n```python\nprint('safe example')\n```".encode()
    ).hexdigest()
    assert task["payload"]["test_plan_sha256"] == _test_plan_hash(candidate_id)
    duplicate = review_store.post(path, json=request)
    assert duplicate.status_code == 202
    assert duplicate.json()["task_id"] == queued.json()["task_id"]

    from app.learning_review_tasks import handle_candidate_sandbox_review
    from app.task_worker import PermanentTaskError
    with db.learning_connection() as connection:
        connection.execute(
            "UPDATE temporary_knowledge_candidates SET test_plan_json=? WHERE id=?",
            (json.dumps({"version": 2, "oracle_reviewed": True, "tests": [{
                "name": "tampered", "python_test_code": "print('changed')", "expected_stdout": "changed",
            }]}), candidate_id),
        )
    class Context:
        task_id = 205
        def checkpoint(self, value): pass
        def control_status(self): return "running"
    with pytest.raises(PermanentTaskError, match="test plan changed"):
        handle_candidate_sandbox_review(task["payload"], None, Context())


def test_candidate_sandbox_worker_runs_reviewed_test_code_and_never_promotes(review_store, monkeypatch, tmp_path):
    from app.learning_review_tasks import handle_candidate_sandbox_review
    from app.sandbox import SandboxResult

    content = "Example lesson.\n\n```python\nprint('safe example')\n```"
    candidate_id, _job_id = _candidate(content=content)
    digest = hashlib.sha256(content.encode()).hexdigest()
    with db.learning_connection() as connection:
        connection.execute(
            "UPDATE temporary_knowledge_candidates SET test_plan_json=? WHERE id=?",
            (json.dumps({"version": 2, "oracle_reviewed": True, "tests": [{"name": "reviewed", "python_test_code": "print('safe example')", "expected_stdout": "ok"}]}), candidate_id),
        )
    docker = tmp_path / "docker"
    docker.write_text("test executable", encoding="utf-8")
    monkeypatch.setenv("SANDBOX_ENABLED", "true")
    monkeypatch.setenv("SANDBOX_DOCKER_EXECUTABLE", str(docker))
    monkeypatch.setenv("SANDBOX_PYTHON_IMAGE", "python:3.12-slim@sha256:" + "a" * 64)
    sandbox_consent.grant_consent()
    calls = []
    monkeypatch.setattr(
        sandbox, "run_python",
        lambda source, **kwargs: (calls.append(source) or SandboxResult("succeeded", 0, "ok", "", 1)),
    )

    class Context:
        task_id = 202
        def checkpoint(self, value): pass
        def control_status(self): return "running"

    result = handle_candidate_sandbox_review(
        {"candidate_id": candidate_id, "content_sha256": digest, "test_plan_sha256": _test_plan_hash(candidate_id), "request_id": "sandbox-request"},
        None,
        Context(),
    )

    assert calls == ["print('safe example')"]
    assert result["status"] == "completed"
    assert result["promoted"] is False
    with db.learning_connection() as connection:
        candidate = connection.execute(
            "SELECT status, trust_level, learning_status FROM temporary_knowledge_candidates WHERE id=?",
            (candidate_id,),
        ).fetchone()
        attempt = connection.execute(
            "SELECT channel, status, details_json FROM knowledge_review_attempts WHERE candidate_id=? ORDER BY id DESC LIMIT 1",
            (candidate_id,),
        ).fetchone()
        ledger_run = connection.execute(
            "SELECT channel, status FROM learning_candidate_channel_runs WHERE candidate_id=? AND channel='sandbox'",
            (candidate_id,),
        ).fetchone()
    assert tuple(candidate) == ("awaiting_test", "L0", "unverified")
    assert tuple(attempt[:2]) == ("sandbox", "completed")
    assert tuple(ledger_run) == ("sandbox", "succeeded")
    assert json.loads(attempt["details_json"])["interpretation"].startswith("Human-attested expected stdout")


def test_candidate_sandbox_records_missing_docker_as_unavailable_not_failed(review_store, monkeypatch, tmp_path):
    from app.learning_review_tasks import handle_candidate_sandbox_review
    from app.sandbox import SandboxResult

    content = "Example lesson.\n\n```python\nprint('safe example')\n```"
    candidate_id, _job_id = _candidate(content=content)
    digest = hashlib.sha256(content.encode()).hexdigest()
    with db.learning_connection() as connection:
        connection.execute(
            "UPDATE temporary_knowledge_candidates SET test_plan_json=? WHERE id=?",
            (json.dumps({"version": 2, "oracle_reviewed": True, "tests": [{"name": "reviewed", "python_test_code": "print('safe example')", "expected_stdout": "ok"}]}), candidate_id),
        )
    docker = tmp_path / "docker"
    docker.write_text("test executable", encoding="utf-8")
    monkeypatch.setenv("SANDBOX_ENABLED", "true")
    monkeypatch.setenv("SANDBOX_DOCKER_EXECUTABLE", str(docker))
    monkeypatch.setenv("SANDBOX_PYTHON_IMAGE", "python:3.12-slim@sha256:" + "a" * 64)
    sandbox_consent.grant_consent()
    monkeypatch.setattr(sandbox, "run_python", lambda *args, **kwargs: SandboxResult("unavailable", None, "", "", 1))

    class Context:
        task_id = 203
        def checkpoint(self, value): pass
        def control_status(self): return "running"

    result = handle_candidate_sandbox_review(
        {"candidate_id": candidate_id, "content_sha256": digest, "test_plan_sha256": _test_plan_hash(candidate_id), "request_id": "sandbox-unavailable"},
        None, Context(),
    )
    assert result["status"] == "unavailable"
    assert result["executed"] == 0
    with db.learning_connection() as connection:
        attempt = connection.execute(
            "SELECT status FROM knowledge_review_attempts WHERE candidate_id=? ORDER BY id DESC LIMIT 1",
            (candidate_id,),
        ).fetchone()
    assert attempt["status"] == "unavailable"


def test_cancelled_sandbox_run_does_not_create_test_failure_experience(review_store, monkeypatch, tmp_path):
    from app.learning_review_tasks import handle_candidate_sandbox_review
    from app.sandbox import SandboxResult

    content = "Lesson stopped by user."
    candidate_id, _job_id = _candidate(content=content)
    digest = hashlib.sha256(content.encode()).hexdigest()
    with db.learning_connection() as connection:
        connection.execute(
            "UPDATE temporary_knowledge_candidates SET test_plan_json=? WHERE id=?",
            (json.dumps({"version": 2, "oracle_reviewed": True, "tests": [{
                "name": "cancelled", "python_test_code": "print('not-run')", "expected_stdout": "not-run",
            }]}), candidate_id),
        )
    docker = tmp_path / "docker"
    docker.write_text("test executable", encoding="utf-8")
    monkeypatch.setenv("SANDBOX_ENABLED", "true")
    monkeypatch.setenv("SANDBOX_DOCKER_EXECUTABLE", str(docker))
    monkeypatch.setenv("SANDBOX_PYTHON_IMAGE", "python:3.12-slim@sha256:" + "a" * 64)
    sandbox_consent.grant_consent()
    monkeypatch.setattr(sandbox, "run_python", lambda *args, **kwargs: SandboxResult("cancelled", None, "", "", 1))

    class Context:
        task_id = 206
        def checkpoint(self, value): pass
        def control_status(self): return "running"

    result = handle_candidate_sandbox_review(
        {"candidate_id": candidate_id, "content_sha256": digest, "test_plan_sha256": _test_plan_hash(candidate_id), "request_id": "sandbox-cancelled"},
        None, Context(),
    )
    assert result["status"] == "unavailable"
    assert result["failure_experience"] is None
    from app.experience import list_experiences
    assert not any(f"Candidate {candidate_id}" in item["problem"] for item in list_experiences(status="failed"))


def test_candidate_sandbox_expected_stdout_mismatch_is_a_failed_test(review_store, monkeypatch, tmp_path):
    from app.learning_review_tasks import handle_candidate_sandbox_review
    from app.sandbox import SandboxResult

    content = "Lesson under test."
    candidate_id, _job_id = _candidate(content=content)
    digest = hashlib.sha256(content.encode()).hexdigest()
    with db.learning_connection() as connection:
        connection.execute(
            "UPDATE temporary_knowledge_candidates SET test_plan_json=? WHERE id=?",
            (json.dumps({"version": 2, "oracle_reviewed": True, "tests": [{
                "name": "exact-output", "python_test_code": "print('actual')", "expected_stdout": "expected",
            }]}), candidate_id),
        )
    docker = tmp_path / "docker"
    docker.write_text("test executable", encoding="utf-8")
    monkeypatch.setenv("SANDBOX_ENABLED", "true")
    monkeypatch.setenv("SANDBOX_DOCKER_EXECUTABLE", str(docker))
    monkeypatch.setenv("SANDBOX_PYTHON_IMAGE", "python:3.12-slim@sha256:" + "a" * 64)
    sandbox_consent.grant_consent()
    monkeypatch.setattr(sandbox, "run_python", lambda *args, **kwargs: SandboxResult("succeeded", 0, "actual", "", 1))

    class Context:
        task_id = 204
        def checkpoint(self, value): pass
        def control_status(self): return "running"

    result = handle_candidate_sandbox_review(
        {"candidate_id": candidate_id, "content_sha256": digest, "test_plan_sha256": _test_plan_hash(candidate_id), "request_id": "sandbox-mismatch"},
        None, Context(),
    )
    assert result["status"] == "failed"
    assert result["outcomes"][0]["passed"] is False
    assert result["failure_experience"]["created"] is True
    with db.learning_connection() as connection:
        candidate = connection.execute(
            "SELECT status, trust_level, learning_status FROM temporary_knowledge_candidates WHERE id=?",
            (candidate_id,),
        ).fetchone()
        attempt = connection.execute(
            "SELECT status FROM knowledge_review_attempts WHERE candidate_id=? ORDER BY id DESC LIMIT 1",
            (candidate_id,),
        ).fetchone()
        ledger_run = connection.execute(
            "SELECT channel, status FROM learning_candidate_channel_runs WHERE candidate_id=? AND channel='sandbox'",
            (candidate_id,),
        ).fetchone()
        experience = connection.execute(
            "SELECT status, problem, test_results_json FROM learning_experiences WHERE id=?",
            (result["failure_experience"]["experience_id"],),
        ).fetchone()
    assert tuple(candidate) == ("awaiting_test", "L0", "unverified")
    assert attempt["status"] == "failed"
    assert tuple(ledger_run) == ("sandbox", "failed")
    assert experience["status"] == "failed"
    assert f"Candidate {candidate_id}" in experience["problem"]
    assert json.loads(experience["test_results_json"])[0]["passed"] is False
    from app.experience import search_relevant_failures
    warnings = search_relevant_failures("sandbox test candidate", limit=5)
    assert any(item["id"] == result["failure_experience"]["experience_id"] for item in warnings)

    from app import learning_candidate_experiences
    repeated = learning_candidate_experiences.record_candidate_test_failure(
        candidate_id,
        topic="Python candidate review",
        content_sha256=digest,
        test_plan_sha256=_test_plan_hash(candidate_id),
        outcomes=result["outcomes"],
    )
    assert repeated["created"] is False
    assert repeated["experience_id"] == result["failure_experience"]["experience_id"]



def test_failed_web_and_peer_channels_become_idempotent_searchable_warnings(review_store, monkeypatch):
    from app import learning_candidate_experiences
    from app.experience import search_relevant_failures

    candidate_id, _job_id = _candidate(topic="Python HTTP retry review")
    checkpoints = []

    class Context:
        task_id = 901
        def checkpoint(self, value): checkpoints.append(value)

    monkeypatch.setattr(review, "research_candidate_online", lambda *args, **kwargs: {
        "status": "failed", "error_type": "SourceFetchError"
    })
    monkeypatch.setattr(review, "consult_configured_peer_ais", lambda *args, **kwargs: {
        "status": "unavailable", "error_type": "ProviderTimeout"
    })

    result = handle_candidate_review(
        {"candidate_id": candidate_id, "query": "Python HTTP retry", "web_mode": "search"},
        None,
        Context(),
    )

    assert result["promoted"] is False
    assert result["channels"]["web_research"]["status"] == "failed"
    assert result["channels"]["peer_ai"]["status"] == "unavailable"
    with db.learning_connection() as connection:
        candidate = connection.execute(
            "SELECT status, trust_level, learning_status FROM temporary_knowledge_candidates WHERE id=?",
            (candidate_id,),
        ).fetchone()
        experiences = connection.execute(
            "SELECT id, topic, problem, status FROM learning_experiences WHERE problem LIKE ? ORDER BY id",
            (f"Candidate {candidate_id} %",),
        ).fetchall()
    assert tuple(candidate) == ("awaiting_test", "L0", "unverified")
    assert len(experiences) == 2
    assert all(item["status"] == "failed" for item in experiences)
    assert any("web research failure" in item["topic"].lower() for item in experiences)
    assert any("peer-ai review failure" in item["topic"].lower() for item in experiences)
    warnings = search_relevant_failures("Python HTTP retry review peer AI web research", limit=3)
    assert {item["id"] for item in warnings}.issuperset({int(item["id"]) for item in experiences})

    digest = hashlib.sha256(b"content").hexdigest()
    duplicate = learning_candidate_experiences.record_candidate_channel_failure(
        candidate_id,
        topic="Python HTTP retry review",
        content_sha256=digest,
        channel="research_web",
        status="failed",
        error_type="SourceFetchError",
    )
    repeated = learning_candidate_experiences.record_candidate_channel_failure(
        candidate_id,
        topic="Python HTTP retry review",
        content_sha256=digest,
        channel="research_web",
        status="failed",
        error_type="SourceFetchError",
    )
    assert duplicate["created"] is True
    assert repeated["created"] is False
    assert duplicate["experience_id"] == repeated["experience_id"]


def test_sandbox_worker_revalidates_curated_oracle_before_execution(review_store, monkeypatch):
    from app.learning_review_tasks import handle_candidate_sandbox_review
    from app.task_worker import PermanentTaskError

    content = "Python can parse JSON."
    candidate_id, _job_id = _candidate(content=content)
    digest = hashlib.sha256(content.encode()).hexdigest()
    plan = {
        "version": 3,
        "oracle_reviewed": True,
        "review_note": "A previously saved plan has an oracle reference.",
        "tests": [{
            "name": "tampered-oracle-claim",
            "objective": "Check a curated Python reference claim.",
            "expected_result": "The curated claim must be present in the candidate.",
            "python_test_code": "print('reviewed')",
            "expected_stdout": "reviewed",
            "oracle_id": "python.json-decode-error",
            "oracle_version": 1,
            "claim": "Python's json.loads raises JSONDecodeError when the input is not a valid JSON document.",
        }],
    }
    with db.learning_connection() as connection:
        connection.execute(
            "UPDATE temporary_knowledge_candidates SET test_plan_json=? WHERE id=?",
            (json.dumps(plan), candidate_id),
        )
    monkeypatch.setattr(
        sandbox,
        "run_python",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("sandbox must not run")),
    )

    class Context:
        task_id = 902
        def checkpoint(self, value): pass
        def control_status(self): return "running"

    with pytest.raises(PermanentTaskError, match="Curated oracle validation failed closed"):
        handle_candidate_sandbox_review(
            {
                "candidate_id": candidate_id,
                "content_sha256": digest,
                "test_plan_sha256": _test_plan_hash(candidate_id),
                "request_id": "oracle-worker-recheck",
            },
            None,
            Context(),
        )
