from __future__ import annotations

import io
import time

import pytest

from app import autonomous_engineering_model_pull as pull


@pytest.fixture(autouse=True)
def reset_pull_state():
    with pull._LOCK:
        pull._JOBS.clear()
        pull._CANCEL_EVENTS.clear()
        pull._RESPONSES.clear()
        pull._ACTIVE_JOB = None
    yield
    with pull._LOCK:
        pull._JOBS.clear()
        pull._CANCEL_EVENTS.clear()
        pull._RESPONSES.clear()
        pull._ACTIVE_JOB = None


def test_model_pull_uses_loopback_ollama_api_and_reports_progress(monkeypatch):
    requests = []

    class FakeOpener:
        def open(self, request, timeout):
            requests.append((request, timeout))
            if request.full_url.endswith("/api/pull"):
                return io.BytesIO(
                    b'{"status":"pulling manifest"}\n'
                    b'{"status":"downloading","completed":5,"total":10}\n'
                    b'{"status":"success"}\n'
                )
            return io.BytesIO(b'{"models":[{"name":"example/model:latest"}]}')

    base_url_calls = []
    monkeypatch.setattr(
        pull, "_base_url",
        lambda: (base_url_calls.append("resolved") or "http://127.0.0.1:11434"),
    )
    monkeypatch.setattr(pull.urllib.request, "build_opener", lambda *args: FakeOpener())

    job = pull.start_model_pull("example/model:latest")
    deadline = time.monotonic() + 3
    state = pull.get_model_pull(job["job_id"])
    while state and state["status"] not in {"completed", "failed"} and time.monotonic() < deadline:
        time.sleep(0.01)
        state = pull.get_model_pull(job["job_id"])

    assert state is not None
    assert state["status"] == "completed"
    assert state["progress"] == 100
    assert requests[0][0].full_url == "http://127.0.0.1:11434/api/pull"
    assert requests[0][0].get_method() == "POST"
    assert requests[0][1] == 60
    assert requests[1][0].full_url == "http://127.0.0.1:11434/api/tags"
    assert requests[1][0].get_method() == "GET"
    assert base_url_calls == ["resolved"]


def test_model_pull_endpoint_resolution_failure_is_reported_and_releases_admission(monkeypatch):
    monkeypatch.setattr(pull, "_base_url", lambda: (_ for _ in ()).throw(RuntimeError("Ollama endpoint invalid")))

    job = pull.start_model_pull("example/model:latest")
    deadline = time.monotonic() + 2
    state = pull.get_model_pull(job["job_id"])
    while state and state["status"] not in {"completed", "failed"} and time.monotonic() < deadline:
        time.sleep(0.01)
        state = pull.get_model_pull(job["job_id"])

    assert state is not None
    assert state["status"] == "failed"
    assert state["error"] == "Ollama endpoint invalid"
    assert pull._ACTIVE_JOB is None


def test_model_pull_does_not_claim_success_when_model_is_absent_from_inventory(monkeypatch):
    class FakeOpener:
        def open(self, request, timeout):
            if request.full_url.endswith("/api/pull"):
                return io.BytesIO(b'{"status":"success"}\n')
            return io.BytesIO(b'{"models":[]}')

    monkeypatch.setattr(pull, "_base_url", lambda: "http://127.0.0.1:11434")
    monkeypatch.setattr(pull.urllib.request, "build_opener", lambda *args: FakeOpener())

    job = pull.start_model_pull("example/model:latest")
    deadline = time.monotonic() + 2
    state = pull.get_model_pull(job["job_id"])
    while state and state["status"] not in {"completed", "failed"} and time.monotonic() < deadline:
        time.sleep(0.01)
        state = pull.get_model_pull(job["job_id"])

    assert state is not None
    assert state["status"] == "failed"
    assert state["error"].startswith("Ollama reported success")
    assert pull._ACTIVE_JOB is None


def test_model_pull_rejects_invalid_model_identifiers():
    for model in ("", "../model", "model with spaces", "model:"):
        with pytest.raises(ValueError, match="شناسهٔ مدل"):
            pull.start_model_pull(model)


def test_model_pull_allows_only_one_active_download(monkeypatch):
    import threading

    started = threading.Event()
    release = threading.Event()

    def wait_for_release(job_id, model):
        started.set()
        release.wait(timeout=2)
        pull._update(job_id, status="completed", progress=100, message="success", error=None)

    monkeypatch.setattr(pull, "_run_pull", wait_for_release)
    first = pull.start_model_pull("example/model:latest")
    assert started.wait(timeout=1)
    with pytest.raises(RuntimeError, match="یک دانلود مدل"):
        pull.start_model_pull("another/model:latest")
    release.set()
    deadline = time.monotonic() + 2
    state = pull.get_model_pull(first["job_id"])
    while state and state["status"] != "completed" and time.monotonic() < deadline:
        time.sleep(0.01)
        state = pull.get_model_pull(first["job_id"])
    assert state and state["status"] == "completed"


def test_model_pull_api_exposes_start_and_status(monkeypatch):
    from app import autonomous_engineering as agent

    started = {
        "job_id": "a" * 32,
        "model": "example/model:latest",
        "status": "queued",
        "progress": 0,
        "message": "queued",
        "error": None,
        "started_at": 1.0,
        "updated_at": 1.0,
    }
    monkeypatch.setattr(agent, "start_model_pull", lambda model: {**started, "model": model})
    monkeypatch.setattr(agent, "get_model_pull", lambda job_id: started if job_id == "a" * 32 else None)

    result = agent.autonomous_engineering_pull_model(
        agent.ModelPullRequest(model="example/model:latest")
    )
    assert result["job_id"] == "a" * 32
    assert result["model"] == "example/model:latest"
    assert agent.autonomous_engineering_model_pull_status("a" * 32) == started

    from fastapi import HTTPException

    with pytest.raises(HTTPException) as missing:
        agent.autonomous_engineering_model_pull_status("b" * 32)
    assert missing.value.status_code == 404


def test_dashboard_exposes_model_pull_controls_and_progress():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    html = (root / "app" / "static" / "autonomous-engineering.html").read_text(encoding="utf-8")
    script = (root / "app" / "static" / "autonomous-engineering-controls.js").read_text(encoding="utf-8")

    assert 'id="pull-model"' in html
    assert 'id="model-pull-status"' in html
    assert 'request("models/pull"' in script
    assert 'request("models/pull/"' in script
    assert "دانلود و نصب مدل انتخاب‌شده" in html

def test_model_pull_cancellation_closes_only_its_stream_and_preserves_existing_models(monkeypatch):
    import threading

    response_ready = threading.Event()
    released = threading.Event()

    class BlockingResponse:
        def __init__(self):
            self.closed = False

        def readline(self, _limit):
            response_ready.set()
            released.wait(timeout=2)
            if self.closed:
                raise OSError("download stream closed")
            return b'{"status":"success"}\\n'

        def close(self):
            self.closed = True
            released.set()

    stream = BlockingResponse()

    class FakeOpener:
        def open(self, request, timeout):
            assert request.full_url.endswith("/api/pull")
            return stream

    monkeypatch.setattr(pull, "_base_url", lambda: "http://127.0.0.1:11434")
    monkeypatch.setattr(pull.urllib.request, "build_opener", lambda *args: FakeOpener())

    job = pull.start_model_pull("example/model:latest")
    assert response_ready.wait(timeout=1)

    cancelling = pull.cancel_model_pull(job["job_id"])
    assert cancelling is not None
    assert cancelling["status"] == "cancelling"
    assert stream.closed is True

    deadline = time.monotonic() + 2
    state = pull.get_model_pull(job["job_id"])
    while state and state["status"] not in {"completed", "failed", "cancelled"} and time.monotonic() < deadline:
        time.sleep(0.01)
        state = pull.get_model_pull(job["job_id"])

    assert state is not None
    assert state["status"] == "cancelled"
    assert pull._ACTIVE_JOB is None


def test_model_pull_cancel_endpoint_exposes_cooperative_stop(monkeypatch):
    from app import autonomous_engineering as agent

    expected = {"job_id": "a" * 32, "status": "cancelling", "message": "stop requested"}
    monkeypatch.setattr(agent, "cancel_model_pull", lambda job_id: expected if job_id == "a" * 32 else None)

    assert agent.autonomous_engineering_cancel_model_pull("a" * 32) == expected

    from fastapi import HTTPException

    with pytest.raises(HTTPException) as missing:
        agent.autonomous_engineering_cancel_model_pull("b" * 32)
    assert missing.value.status_code == 404

