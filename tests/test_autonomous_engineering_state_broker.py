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


def test_broker_pipe_io_uses_overlapped_handles_and_a_shared_deadline():
    client = (ROOT / "app" / "autonomous_engineering_state_broker.py").read_text(encoding="utf-8")

    assert "_FILE_FLAG_OVERLAPPED = 0x40000000" in client
    assert "_overlapped_transfer(" in client
    assert "WaitForSingleObject" in client
    assert "CancelIoEx" in client
    assert "deadline = time.monotonic() + timeout_ms / 1000.0" in client
    assert "size=len(chunk), deadline=deadline" in client
    assert "size=len(payload), deadline=deadline" in client


def test_broker_pipe_io_cancels_a_timed_out_operation(monkeypatch):
    class FakeKernel:
        def __init__(self):
            self.waits = 0
            self.cancelled = False
            self.closed = False

        def CreateEventW(self, *_args):
            return 7

        def WriteFile(self, *_args):
            return 0

        def WaitForSingleObject(self, *_args):
            self.waits += 1
            return 258 if self.waits == 1 else 0

        def CancelIoEx(self, *_args):
            self.cancelled = True
            return 1

        def CloseHandle(self, _handle):
            self.closed = True
            return 1

    fake = FakeKernel()
    monkeypatch.setattr(broker.ctypes, "get_last_error", lambda: 997, raising=False)
    with pytest.raises(broker.StateBrokerError, match="timed out"):
        broker._overlapped_transfer(
            fake, 11, write=True, buffer=object(), size=1,
            deadline=broker.time.monotonic() + 1,
        )
    assert fake.cancelled
    assert fake.closed
    assert fake.waits == 2


def test_broker_pipe_io_treats_peer_close_as_end_of_response(monkeypatch):
    class FakeKernel:
        def __init__(self):
            self.closed = False

        def CreateEventW(self, *_args):
            return 7

        def ReadFile(self, *_args):
            return 0

        def CloseHandle(self, _handle):
            self.closed = True
            return 1

    fake = FakeKernel()
    monkeypatch.setattr(broker.ctypes, "get_last_error", lambda: 109, raising=False)
    result = broker._overlapped_transfer(
        fake, 11, write=False, buffer=object(), size=8,
        deadline=broker.time.monotonic() + 1,
    )
    assert result == 0
    assert fake.closed


@pytest.mark.parametrize("timeout_ms", [True, False, 0, 249, 30001, 3.5, "3500"])
def test_broker_client_rejects_invalid_io_deadlines(timeout_ms):
    with pytest.raises(ValueError, match="timeout"):
        broker.request("GET", "/v1/health", timeout_ms=timeout_ms)


def test_broker_pipe_io_treats_pending_broken_pipe_completion_as_eof(monkeypatch):
    class FakeKernel:
        def __init__(self):
            self.closed = False

        def CreateEventW(self, *_args):
            return 7

        def ReadFile(self, *_args):
            return 0

        def WaitForSingleObject(self, *_args):
            return 0

        def GetOverlappedResult(self, *_args):
            return 0

        def CloseHandle(self, _handle):
            self.closed = True
            return 1

    errors = iter([997, 109])
    monkeypatch.setattr(broker.ctypes, "get_last_error", lambda: next(errors), raising=False)
    fake = FakeKernel()
    result = broker._overlapped_transfer(
        fake, 11, write=False, buffer=object(), size=8,
        deadline=broker.time.monotonic() + 1,
    )
    assert result == 0
    assert fake.closed


def test_broker_pipe_io_does_not_start_when_deadline_has_expired():
    class FakeKernel:
        def CreateEventW(self, *_args):
            raise AssertionError("An expired operation must not allocate or start pipe I/O.")

    with pytest.raises(broker.StateBrokerError, match="timed out"):
        broker._overlapped_transfer(
            FakeKernel(), 11, write=False, buffer=object(), size=8,
            deadline=broker.time.monotonic() - 1,
        )
