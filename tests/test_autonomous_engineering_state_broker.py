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



def test_python_broker_client_accepts_only_fixed_protocol_endpoints(monkeypatch):
    endpoints = [
        ("GET", "/v1/health"),
        ("GET", "/v1/pending-work"),
        ("GET", "/v1/progress"),
        ("GET", "/v1/diagnostics/44083557068"),
        ("POST", "/v1/cleanup"),
    ]

    if broker.os.name == "nt":
        def stop_before_pipe_io():
            raise broker.StateBrokerError("transport reached")

        monkeypatch.setattr(broker, "_windows_api", stop_before_pipe_io)
        for method, path in endpoints:
            with pytest.raises(broker.StateBrokerError, match="transport reached"):
                broker.request(method, path)
    else:
        from app import autonomous_engineering_state_broker_unix as unix

        calls = []

        def fake_request_unix(method, path, **kwargs):
            calls.append((method, path, kwargs))
            return {"accepted": path}

        monkeypatch.setattr(unix, "request_unix", fake_request_unix)
        for method, path in endpoints:
            assert broker.request(method, path) == {"accepted": path}

        assert [(method, path) for method, path, _ in calls] == endpoints
        assert all(call[2]["timeout_ms"] == 3500 for call in calls)


def test_broker_client_validates_dispatch_admission_body_before_transport(monkeypatch):
    from app import autonomous_engineering_state_broker_unix as unix

    calls = []
    monkeypatch.setattr(unix, "request_unix", lambda method, path, **kwargs: (
        calls.append((method, path, kwargs)) or {"acquired": True}
    ))
    if broker.os.name == "nt":
        pytest.skip("The POSIX transport seam is exercised on POSIX; Windows contract is covered by service tests.")
    request_id = "a" * 32
    result = broker.request(
        "POST", "/v1/dispatch-admission/acquire",
        body={"request_id": request_id},
    )
    assert result == {"acquired": True}
    assert calls[0][0:2] == ("POST", "/v1/dispatch-admission/acquire")
    assert calls[0][2]["body"] == {"request_id": request_id}
    with pytest.raises(ValueError):
        broker.request(
            "POST", "/v1/dispatch-admission/acquire",
            body={"request_id": "../state"},
        )



def test_windows_broker_dispatch_admission_protocol_roundtrip():
    if broker.os.name != "nt":
        pytest.skip("Windows named-pipe integration only.")
    if not broker.health(timeout_ms=1500):
        pytest.skip("Protected Windows state broker is not installed in this test environment.")
    first_id = uuid.uuid4().hex
    second_id = uuid.uuid4().hex
    try:
        broker.request("POST", "/v1/dispatch-admission/acquire", body={"request_id": first_id})
    except broker.StateBrokerConflict:
        pytest.skip("The shared broker currently has an active reservation or grace window.")
    with pytest.raises(broker.StateBrokerConflict):
        broker.request("POST", "/v1/dispatch-admission/acquire", body={"request_id": second_id})
    broker.request(
        "POST", "/v1/dispatch-admission/complete",
        body={"request_id": first_id, "accepted": False},
    )
    broker.request("POST", "/v1/dispatch-admission/acquire", body={"request_id": second_id})
    broker.request(
        "POST", "/v1/dispatch-admission/complete",
        body={"request_id": second_id, "accepted": True},
    )
    with pytest.raises(broker.StateBrokerConflict):
        broker.request("POST", "/v1/dispatch-admission/acquire", body={"request_id": "c" * 32})



def test_broker_dispatch_admission_is_allowlisted_in_both_service_transports():
    client = (ROOT / "app" / "autonomous_engineering_state_broker.py").read_text(encoding="utf-8")
    unix = (ROOT / "app" / "autonomous_engineering_state_broker_unix.py").read_text(encoding="utf-8")
    windows = (ROOT / "scripts" / "AutonomousStateBrokerService.cs").read_text(encoding="utf-8")
    posix = (ROOT / "app" / "autonomous_engineering_state_store.py").read_text(encoding="utf-8")

    for source in (client, unix):
        assert '"/v1/dispatch-admission/acquire"' in source
        assert '"/v1/dispatch-admission/complete"' in source
        assert "validate_dispatch_admission_request" in source
    assert "HandleDispatchAdmission" in windows
    assert '"workflow-dispatch-admission.json"' in windows
    assert "WithStateLock" in windows[windows.index("private void HandleDispatchAdmission"):windows.index("private object CleanupProgress")]
    assert "dispatch_admission" in posix
    assert "workflow-dispatch-admission.json" in posix
    assert "lease_expires_at" in posix



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
    assert 'path == "/v1/dispatch-admission/acquire"' in service
    assert 'path == "/v1/dispatch-admission/complete"' in service
    assert 'path.StartsWith("/v1/diagnostics/"' in service
    assert "ReadCheckpointSummary" in service
    assert "latest-worktree-checkpoint.json" in service
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
    assert result["error"] == "Shared progress storage is unavailable: pipe unavailable"


def test_broker_pipe_io_uses_overlapped_handles_and_a_shared_deadline():
    client = (ROOT / "app" / "autonomous_engineering_state_broker.py").read_text(encoding="utf-8")

    assert "_FILE_FLAG_OVERLAPPED = 0x40000000" in client
    assert "_SECURITY_SQOS_PRESENT = 0x00100000" in client
    assert "_SECURITY_IMPERSONATION = 0x00020000" in client
    assert "_PIPE_OPEN_FLAGS = _FILE_FLAG_OVERLAPPED | _SECURITY_SQOS_PRESENT | _SECURITY_IMPERSONATION" in client
    assert client.count("_PIPE_OPEN_FLAGS,") == 1
    assert "_ERROR_FILE_NOT_FOUND = 2" in client
    assert "def _open_pipe(" in client
    assert "handle = _open_pipe(kernel32, deadline)" in client
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


def test_broker_pipe_open_retries_transient_missing_instance(monkeypatch):
    class FakeKernel:
        def __init__(self):
            self.calls = 0

        def CreateFileW(self, *_args):
            self.calls += 1
            return broker._INVALID_HANDLE_VALUE if self.calls == 1 else 77

        def WaitNamedPipeW(self, *_args):
            raise AssertionError("A missing pipe instance should be retried without an unbounded wait.")

    fake = FakeKernel()
    monkeypatch.setattr(broker.ctypes, "get_last_error", lambda: 2, raising=False)
    monkeypatch.setattr(broker.time, "sleep", lambda _seconds: None)
    assert broker._open_pipe(fake, broker.time.monotonic() + 1) == 77
    assert fake.calls == 2


def test_broker_pipe_open_waits_for_busy_instance(monkeypatch):
    class FakeKernel:
        def __init__(self):
            self.calls = 0
            self.waited = False

        def CreateFileW(self, *_args):
            self.calls += 1
            return broker._INVALID_HANDLE_VALUE if self.calls == 1 else 88

        def WaitNamedPipeW(self, _name, timeout):
            assert timeout > 0
            self.waited = True
            return 1

    fake = FakeKernel()
    monkeypatch.setattr(broker.ctypes, "get_last_error", lambda: 231, raising=False)
    assert broker._open_pipe(fake, broker.time.monotonic() + 1) == 88
    assert fake.waited
    assert fake.calls == 2


def test_broker_pipe_acl_grants_local_service_minimal_server_instance_rights():
    service = (ROOT / "scripts" / "AutonomousStateBrokerService.cs").read_text(encoding="utf-8")

    local_service = service.index('new SecurityIdentifier("S-1-5-19")')
    server_rights = service.index(
        "PipeAccessRights.CreateNewInstance | PipeAccessRights.ReadWrite",
        local_service,
    )
    authenticated_users = service.index('new SecurityIdentifier("S-1-5-11")')
    network_deny = service.index('new SecurityIdentifier("S-1-5-2")')

    assert network_deny < local_service < server_rights < authenticated_users
    assert "AccessControlType.Deny" in service[network_deny:local_service]
    assert "AccessControlType.Allow" in service[local_service:authenticated_users]


def test_broker_live_windows_integration_rejects_worker_loop_diagnostics():
    service_test = (ROOT / "scripts" / "test_autonomous_state_broker_service.ps1").read_text(encoding="utf-8")

    assert '$workerDiagnosticsPath = Join-Path $stateRoot "service-errors.log"' in service_test
    assert "recorded unexpected exceptions" in service_test

def test_broker_cleanup_enforces_repository_state_and_checkpoint_lock_order():
    service = (ROOT / "scripts" / "AutonomousStateBrokerService.cs").read_text(encoding="utf-8")
    cleanup_start = service.index("private object CleanupProgress()")
    repository_lock = service.index("WithRepositoryExecutionLock", cleanup_start)
    state_lock = service.index("WithStateLock", repository_lock)
    checkpoint_lock = service.index("WithCheckpointStoreLock", state_lock)
    repository_lock_method = service.index("private object WithRepositoryExecutionLock")

    assert cleanup_start < repository_lock < state_lock < checkpoint_lock < repository_lock_method
    assert 'Path.Combine(_stateDirectory, "repository-execution.lock")' in service
    assert "stream.Lock(0, 1)" in service
    assert "stream.Unlock(0, 1)" in service
    assert '"repository_execution_active"' in service
    assert "An autonomous Runner owns the repository execution lock" in service
    assert 'Path.Combine(_stateDirectory, "current.json")' in service
    assert '"active_progress_run"' in service
    assert "RecoverStaleProgress(current, out recoveredRunId)" in service
    assert "InspectRepositoryOwnerProcess" in service
    assert 'owner.TryGetValue("process_started_at"' in service
    assert 'owner.TryGetValue("owner_token"' in service
    assert 'current.TryGetValue("execution_owner_token"' in service
    assert 'processState != "missing" && processState != "reused"' in service
    assert '"progress_recovery"' in service
    assert '"interrupted"' in service
    cleanup_start = service.index("private object CleanupProgress()")
    cleanup_end = service.index("private object WithRepositoryExecutionLock", cleanup_start)
    cleanup = service[cleanup_start:cleanup_end]
    cleanup_names_start = cleanup.index("string[] cleanupNames")
    cleanup_names_end = cleanup.index("};", cleanup_names_start)
    cleanup_names = cleanup[cleanup_names_start:cleanup_names_end]
    assert '"current.json",' not in cleanup_names
    assert '"events.jsonl",' not in cleanup_names
    assert '"current.json"' in cleanup
    assert '"events.jsonl"' in cleanup
    assert "FileShare.Read | FileShare.Delete" in service
    assert '"progress_writer_active"' in service
    assert "A progress writer is active" in service


def test_python_broker_health_preserves_posix_and_extends_windows_contract(monkeypatch):
    captured = {}

    def healthy_request(method, path, **kwargs):
        captured.update(method=method, path=path, kwargs=kwargs)
        return {"ready": True}

    monkeypatch.setattr(broker, "request", healthy_request)
    monkeypatch.setattr(broker, "_WINDOWS", False)
    assert broker.health(timeout_ms=30000, verify=True) is True
    assert captured == {
        "method": "GET",
        "path": "/v1/health",
        "kwargs": {"timeout_ms": 30000},
    }

    monkeypatch.setattr(broker, "_WINDOWS", True)
    captured.clear()
    monkeypatch.setattr(
        broker, "request",
        lambda method, path, **kwargs: captured.update(
            method=method, path=path, kwargs=kwargs
        ) or {"ready": True, "storage_ready": True, "acl_ready": True},
    )
    assert broker.health(timeout_ms=30000, verify=True) is True
    assert captured == {
        "method": "GET",
        "path": "/v1/health/verify",
        "kwargs": {"timeout_ms": 30000},
    }

    monkeypatch.setattr(
        broker, "request",
        lambda *_args, **_kwargs: {
            "ready": True, "storage_ready": True, "acl_ready": False,
        },
    )
    assert broker.health(timeout_ms=30000) is False

    monkeypatch.setattr(
        broker, "request",
        lambda *_args, **_kwargs: {
            "ready": True, "storage_ready": False, "acl_ready": True,
        },
    )
    assert broker.health(timeout_ms=30000) is False


def test_broker_pipe_open_reports_missing_service_instead_of_hiding_it_as_timeout(monkeypatch):
    class MissingServiceKernel:
        def __init__(self):
            self.calls = 0

        def CreateFileW(self, *_args):
            self.calls += 1
            return broker._INVALID_HANDLE_VALUE

    fake = MissingServiceKernel()
    monkeypatch.setattr(broker.ctypes, "get_last_error", lambda: broker._ERROR_FILE_NOT_FOUND, raising=False)
    monkeypatch.setattr(broker.time, "sleep", lambda _seconds: None)

    with pytest.raises(broker.StateBrokerError, match=r"pipe is unavailable \(WinError 2\)"):
        broker._open_pipe(fake, broker.time.monotonic() + 5)

    assert fake.calls == broker._ERROR_FILE_NOT_FOUND_RETRIES


def test_broker_pipe_open_waits_for_busy_instance_repeated_regression(monkeypatch):
    class FakeKernel:
        def __init__(self):
            self.calls = 0
            self.waited = False

        def CreateFileW(self, *_args):
            self.calls += 1
            return broker._INVALID_HANDLE_VALUE if self.calls == 1 else 88

        def WaitNamedPipeW(self, _name, timeout):
            assert timeout > 0
            self.waited = True
            return 1

    fake = FakeKernel()
    monkeypatch.setattr(broker.ctypes, "get_last_error", lambda: 231, raising=False)
    assert broker._open_pipe(fake, broker.time.monotonic() + 1) == 88
    assert fake.waited
    assert fake.calls == 2


def test_broker_pipe_acl_grants_local_service_minimal_server_instance_rights_repeated_regression():
    service = (ROOT / "scripts" / "AutonomousStateBrokerService.cs").read_text(encoding="utf-8")

    local_service = service.index('new SecurityIdentifier("S-1-5-19")')
    server_rights = service.index(
        "PipeAccessRights.CreateNewInstance | PipeAccessRights.ReadWrite",
        local_service,
    )
    authenticated_users = service.index('new SecurityIdentifier("S-1-5-11")')
    network_deny = service.index('new SecurityIdentifier("S-1-5-2")')

    assert network_deny < local_service < server_rights < authenticated_users
    assert "AccessControlType.Deny" in service[network_deny:local_service]
    assert "AccessControlType.Allow" in service[local_service:authenticated_users]


def test_broker_live_windows_integration_rejects_worker_loop_diagnostics_repeated_regression():
    service_test = (ROOT / "scripts" / "test_autonomous_state_broker_service.ps1").read_text(encoding="utf-8")

    assert '$workerDiagnosticsPath = Join-Path $stateRoot "service-errors.log"' in service_test
    assert "recorded unexpected exceptions" in service_test

def test_broker_cleanup_enforces_repository_state_and_checkpoint_lock_order_repeated_regression():
    service = (ROOT / "scripts" / "AutonomousStateBrokerService.cs").read_text(encoding="utf-8")
    cleanup_start = service.index("private object CleanupProgress()")
    repository_lock = service.index("WithRepositoryExecutionLock", cleanup_start)
    state_lock = service.index("WithStateLock", repository_lock)
    checkpoint_lock = service.index("WithCheckpointStoreLock", state_lock)
    repository_lock_method = service.index("private object WithRepositoryExecutionLock")

    assert cleanup_start < repository_lock < state_lock < checkpoint_lock < repository_lock_method
    assert 'Path.Combine(_stateDirectory, "repository-execution.lock")' in service
    assert "stream.Lock(0, 1)" in service
    assert "stream.Unlock(0, 1)" in service
    assert '"repository_execution_active"' in service
    assert "An autonomous Runner owns the repository execution lock" in service
    assert 'Path.Combine(_stateDirectory, "current.json")' in service
    assert '"active_progress_run"' in service
    recovery_check = service.index("if (!RecoverStaleProgress(current, out recoveredRunId))", checkpoint_lock)
    conflict_check = service.index("if (blockers.Count > 0)", recovery_check)
    assert recovery_check < service.index('blockers.Add("active_progress_run")', recovery_check) < conflict_check
    assert "FileShare.Read | FileShare.Delete" in service
    assert '"progress_writer_active"' in service
    assert "A progress writer is active" in service


def test_python_broker_health_preserves_posix_and_extends_windows_contract_repeated_regression(monkeypatch):
    captured = {}

    def healthy_request(method, path, **kwargs):
        captured.update(method=method, path=path, kwargs=kwargs)
        return {"ready": True}

    monkeypatch.setattr(broker, "request", healthy_request)
    monkeypatch.setattr(broker, "_WINDOWS", False)
    assert broker.health(timeout_ms=30000, verify=True) is True
    assert captured == {
        "method": "GET",
        "path": "/v1/health",
        "kwargs": {"timeout_ms": 30000},
    }

    monkeypatch.setattr(broker, "_WINDOWS", True)
    captured.clear()
    monkeypatch.setattr(
        broker, "request",
        lambda method, path, **kwargs: captured.update(
            method=method, path=path, kwargs=kwargs
        ) or {"ready": True, "storage_ready": True, "acl_ready": True},
    )
    assert broker.health(timeout_ms=30000, verify=True) is True
    assert captured == {
        "method": "GET",
        "path": "/v1/health/verify",
        "kwargs": {"timeout_ms": 30000},
    }

    monkeypatch.setattr(
        broker, "request",
        lambda *_args, **_kwargs: {
            "ready": True, "storage_ready": True, "acl_ready": False,
        },
    )
    assert broker.health(timeout_ms=30000) is False

    monkeypatch.setattr(
        broker, "request",
        lambda *_args, **_kwargs: {
            "ready": True, "storage_ready": False, "acl_ready": True,
        },
    )
    assert broker.health(timeout_ms=30000) is False




def test_broker_cleanup_failures_are_returned_as_protocol_errors_instead_of_closing_the_pipe():
    service = (ROOT / "scripts" / "AutonomousStateBrokerService.cs").read_text(encoding="utf-8")
    route = service[service.index('if (method == "POST" && path == "/v1/cleanup")'):]

    assert "catch (Exception exception)" in route
    assert "string code = CleanupFailureCode(exception)" in route
    assert "WriteResponse(pipe, 503, Error(code))" in route
    assert "cleanup_access_denied" in service
    assert "state_directory_missing" in service
    assert "durable_state_unverified" in service
    assert "cleanup_lock_timeout" in service
    assert "cleanup_io_failed" in service
    assert "cleanup_failed" in service
