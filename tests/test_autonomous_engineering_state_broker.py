from pathlib import Path

import pytest

from app import autonomous_engineering_state_broker as broker


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("DELETE", "/v1/progress"),
        ("GET", "/v1/"),
        ("GET", "/v1/files/current.json"),
        ("GET", "/v1/progress/../../secrets"),
        ("GET", "/v1/diagnostics/12/../../other"),
        ("GET", "/v1/diagnostics/not-a-run-id"),
        ("POST", "/v1/pending-work"),
        ("POST", "/v1/cleanup/anything"),
    ],
)
def test_python_broker_client_rejects_unlisted_methods_and_paths_before_connecting(method, path):
    with pytest.raises(ValueError, match="Unsupported state-broker"):
        broker.request(method, path)


def test_python_broker_client_accepts_only_fixed_protocol_endpoints():
    # Endpoint validation occurs before the Windows-only pipe open.
    with pytest.raises(broker.StateBrokerError, match="only on Windows"):
        broker.request("GET", "/v1/health")
    with pytest.raises(broker.StateBrokerError, match="only on Windows"):
        broker.request("GET", "/v1/pending-work")
    with pytest.raises(broker.StateBrokerError, match="only on Windows"):
        broker.request("GET", "/v1/progress")
    with pytest.raises(broker.StateBrokerError, match="only on Windows"):
        broker.request("GET", "/v1/diagnostics/44083557068")
    with pytest.raises(broker.StateBrokerError, match="only on Windows"):
        broker.request("POST", "/v1/cleanup")


def test_windows_app_state_paths_are_mediated_by_the_broker():
    progress = (ROOT / "app" / "autonomous_engineering_progress.py").read_text(encoding="utf-8")
    agent = (ROOT / "app" / "autonomous_engineering.py").read_text(encoding="utf-8")
    client = (ROOT / "app" / "autonomous_engineering_state_broker.py").read_text(encoding="utf-8")

    assert "from .autonomous_engineering_state_broker import health" in progress
    assert "from .autonomous_engineering_state_broker import pending_work" in progress
    assert "from .autonomous_engineering_state_broker import progress" in progress
    assert "from .autonomous_engineering_state_broker import diagnostics" in progress
    assert "from .autonomous_engineering_state_broker import cleanup" in progress
    assert "cleanup_shared_progress_data" in agent
    assert "_MAX_RESPONSE_BYTES = 16 * 1024 * 1024" in client


def test_broker_service_has_no_generic_file_path_operation():
    service = (ROOT / "scripts" / "AutonomousStateBrokerService.cs").read_text(encoding="utf-8")

    assert 'path == "/v1/health"' in service
    assert 'path == "/v1/pending-work"' in service
    assert 'path == "/v1/progress"' in service
    assert 'path == "/v1/cleanup"' in service
    assert 'path.StartsWith("/v1/diagnostics/"' in service
    assert "request.ContainsKey(\"body\")" in service
    assert "Directory.GetFiles" not in service
    assert "Directory.GetDirectories" not in service
    assert "TcpListener" not in service
    assert "File.ReadAllText(path" in service



def test_progress_reader_returns_degraded_payload_when_broker_is_unavailable(monkeypatch):
    from app import autonomous_engineering_progress as progress

    def unavailable():
        raise broker.StateBrokerError("pipe unavailable")

    monkeypatch.setattr(progress, "_shared_state_snapshot", unavailable)
    result = progress.get_progress()

    assert result["available"] is False
    assert result["storage_configured"] is False
    assert result["current"] is None
    assert result["history"] == []
    assert "StateBrokerError" in result["error"]
