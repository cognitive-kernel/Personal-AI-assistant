from __future__ import annotations

import pytest

from app import autonomous_engineering_state_broker as broker


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("GET", "/v1/tasks/pending", None),
        ("GET", "/v1/tasks/archived", None),
        ("POST", "/v1/tasks/archive", {"task_id": "a" * 64, "revision": "b" * 64}),
        ("POST", "/v1/tasks/restore", {"task_id": "a" * 64, "revision": "b" * 64}),
    ],
)
def test_python_client_accepts_only_pending_task_contract_shapes(monkeypatch, method, path, body):
    if broker.os.name == "nt":
        def stop_before_pipe_io():
            raise broker.StateBrokerError("transport reached")
        monkeypatch.setattr(broker, "_windows_api", stop_before_pipe_io)
        with pytest.raises(broker.StateBrokerError, match="transport reached"):
            broker.request(method, path, body=body)
    else:
        from app import autonomous_engineering_state_broker_unix as unix
        calls = []
        monkeypatch.setattr(unix, "request_unix", lambda m, p, **kw: calls.append((m, p, kw)) or {})
        broker.request(method, path, body=body)
        assert calls[0][0:2] == (method, path)
        assert calls[0][2]["body"] == body


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("POST", "/v1/tasks/archive", {"task_id": "../task", "revision": "b" * 64}),
        ("POST", "/v1/tasks/archive", {"task_id": "a" * 64, "revision": "b" * 64, "path": "x"}),
        ("GET", "/v1/tasks/pending", {"path": "/etc/passwd"}),
        ("POST", "/v1/tasks/purge", {"task_id": "a" * 64, "revision": "b" * 64}),
    ],
)
def test_python_client_rejects_unsafe_pending_task_shapes_before_transport(monkeypatch, method, path, body):
    def unexpected_transport():
        raise AssertionError("Invalid contract requests must be rejected before transport.")
    if broker.os.name == "nt":
        monkeypatch.setattr(broker, "_windows_api", unexpected_transport)
    else:
        from app import autonomous_engineering_state_broker_unix as unix
        monkeypatch.setattr(unix, "request_unix", unexpected_transport)
    with pytest.raises(ValueError):
        broker.request(method, path, body=body)
