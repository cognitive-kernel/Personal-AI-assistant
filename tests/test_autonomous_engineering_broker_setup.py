from __future__ import annotations

import multiprocessing
import os
import shutil
import subprocess
import time
import uuid

import pytest

from app import autonomous_engineering_broker_setup as setup



def _try_setup_process_lock_from_child(result_queue):
    with setup._setup_process_lock() as acquired:
        result_queue.put(acquired)


def test_broker_setup_is_idempotent_when_health_check_is_already_ready(monkeypatch):
    calls = []
    monkeypatch.setattr(setup, "_broker_is_healthy", lambda: True)
    monkeypatch.setattr(setup.subprocess, "run", lambda *args, **kwargs: calls.append(args))

    result = setup.setup_state_broker()

    assert result["ready"] is True
    assert result["already_ready"] is True
    assert calls == []


def test_windows_broker_setup_uses_the_selected_compatible_host(monkeypatch, tmp_path):
    health = iter((False, True))
    monkeypatch.setattr(setup, "_broker_is_healthy", lambda: next(health))
    monkeypatch.setattr(setup, "_repository_root", lambda: tmp_path)
    monkeypatch.setattr(setup.platform, "system", lambda: "Windows")
    command = [r"C:\PowerShell\pwsh.exe", "-NoLogo", "-File", str(tmp_path / "ensure.ps1")]
    monkeypatch.setattr(setup, "_windows_setup_command", lambda root: (command, "Core 8.x"))
    captured = {}

    def fake_run(actual, **kwargs):
        captured["command"] = actual
        captured["kwargs"] = kwargs
        return subprocess.CompletedProcess(actual, 0, "ok", "")

    monkeypatch.setattr(setup.subprocess, "run", fake_run)

    result = setup.setup_state_broker()

    assert result["ready"] is True
    assert result["runtime"] == "Core 8.x"
    assert captured["command"] == command
    assert captured["kwargs"]["shell"] is False
    assert captured["kwargs"]["stdin"] == subprocess.DEVNULL
    assert captured["kwargs"]["timeout"] == setup._SETUP_TIMEOUT_SECONDS


def test_broker_setup_reports_uac_cancellation_without_retrying(monkeypatch, tmp_path):
    monkeypatch.setattr(setup, "_broker_is_healthy", lambda: False)
    monkeypatch.setattr(setup, "_repository_root", lambda: tmp_path)
    monkeypatch.setattr(setup.platform, "system", lambda: "Windows")
    monkeypatch.setattr(setup, "_windows_setup_command", lambda root: (["pwsh.exe"], "Core 8.x"))
    monkeypatch.setattr(
        setup.subprocess,
        "run",
        lambda command, **kwargs: subprocess.CompletedProcess(
            command, 1, "", "The operation was canceled by the user."
        ),
    )

    with pytest.raises(setup.BrokerSetupError) as error:
        setup.setup_state_broker()

    assert error.value.status_code == 403
    assert error.value.code == "STATE_BROKER_ELEVATION_CANCELLED"
    assert error.value.retryable is False


def test_broker_setup_reports_timeout_as_ambiguous_and_never_retries(monkeypatch, tmp_path):
    monkeypatch.setattr(setup, "_broker_is_healthy", lambda: False)
    monkeypatch.setattr(setup, "_repository_root", lambda: tmp_path)
    monkeypatch.setattr(setup.platform, "system", lambda: "Windows")
    monkeypatch.setattr(setup, "_windows_setup_command", lambda root: (["pwsh.exe"], "Core 8.x"))

    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], kwargs["timeout"])

    monkeypatch.setattr(setup.subprocess, "run", timeout)

    with pytest.raises(setup.BrokerSetupError) as error:
        setup.setup_state_broker()

    assert error.value.status_code == 504
    assert error.value.code == "STATE_BROKER_SETUP_TIMEOUT"
    assert error.value.retryable is False


def test_broker_setup_lock_rejects_a_concurrent_request(monkeypatch):
    monkeypatch.setattr(setup, "_broker_is_healthy", lambda: False)
    assert setup._SETUP_LOCK.acquire(blocking=False)
    try:
        with pytest.raises(setup.BrokerSetupError) as error:
            setup.setup_state_broker()
    finally:
        setup._SETUP_LOCK.release()

    assert error.value.status_code == 409
    assert error.value.code == "STATE_BROKER_SETUP_IN_PROGRESS"


def test_broker_setup_does_not_claim_success_when_post_install_health_fails(monkeypatch, tmp_path):
    health = iter((False, False))
    monkeypatch.setattr(setup, "_broker_is_healthy", lambda: next(health))
    monkeypatch.setattr(setup, "_repository_root", lambda: tmp_path)
    monkeypatch.setattr(setup.platform, "system", lambda: "Windows")
    monkeypatch.setattr(setup, "_windows_setup_command", lambda root: (["pwsh.exe"], "Core 8.x"))
    monkeypatch.setattr(
        setup.subprocess,
        "run",
        lambda command, **kwargs: subprocess.CompletedProcess(command, 0, "", ""),
    )

    with pytest.raises(setup.BrokerSetupError) as error:
        setup.setup_state_broker()

    assert error.value.code == "STATE_BROKER_POST_INSTALL_HEALTH_FAILED"
    assert error.value.status_code == 503


def test_broker_setup_lock_excludes_a_second_process(monkeypatch, tmp_path):
    for key in ("TMP", "TEMP", "TMPDIR"):
        monkeypatch.setenv(key, str(tmp_path))
    monkeypatch.setattr(setup.tempfile, "tempdir", None)
    context = multiprocessing.get_context("spawn")
    result_queue = context.Queue()

    with setup._setup_process_lock() as acquired:
        assert acquired is True
        child = context.Process(
            target=_try_setup_process_lock_from_child,
            args=(result_queue,),
        )
        child.start()
        try:
            assert result_queue.get(timeout=20) is False
        finally:
            child.join(timeout=20)
            if child.is_alive():
                child.terminate()
                child.join(timeout=5)
            result_queue.close()
            result_queue.join_thread()
        assert child.exitcode == 0


@pytest.mark.skipif(os.name != "nt", reason="Windows named-mutex ownership is platform-specific")
def test_elevated_child_mutex_survives_parent_exit_and_reports_abandoned_owner(tmp_path):
    powershell = shutil.which("powershell") or shutil.which("pwsh")
    if not powershell:
        pytest.skip("A supported PowerShell host is unavailable")

    mutex_name = "Global\\PersonalAIAssistant.StateBrokerSetup.Test." + uuid.uuid4().hex
    marker = str(tmp_path / "mutex-held.txt").replace("'", "''")
    hold_script = (
        "$mutex = [System.Threading.Mutex]::new($false, '" + mutex_name + "'); "
        "if (-not $mutex.WaitOne(0)) { exit 3 }; "
        "[System.IO.File]::WriteAllText('" + marker + "', 'held'); "
        "Start-Sleep -Seconds 30"
    )
    child = subprocess.Popen(
        [powershell, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", hold_script],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and not os.path.exists(marker):
            if child.poll() is not None:
                stdout, stderr = child.communicate(timeout=2)
                pytest.fail(f"Mutex-owning child exited early: {stdout} {stderr}")
            time.sleep(0.05)
        assert os.path.exists(marker), "Mutex-owning child did not acquire the named mutex"

        competing_script = (
            "$mutex = [System.Threading.Mutex]::new($false, '" + mutex_name + "'); "
            "try { "
            "$acquired = $mutex.WaitOne(0); "
            "if ($acquired) { $mutex.ReleaseMutex(); exit 4 }; "
            "exit 2 "
            "} finally { $mutex.Dispose() }"
        )
        competing = subprocess.run(
            [powershell, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", competing_script],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        assert competing.returncode == 2, competing.stdout + competing.stderr

        # Terminating the non-owner test process cannot release the child-owned
        # mutex. Only terminating the owner makes the next acquisition abandoned.
        child.terminate()
        child.wait(timeout=5)
        abandoned_script = (
            "$mutex = [System.Threading.Mutex]::new($false, '" + mutex_name + "'); "
            "try { "
            "try { "
            "$acquired = $mutex.WaitOne(0); "
            "if ($acquired) { $mutex.ReleaseMutex(); exit 4 }; "
            "exit 2 "
            "} catch [System.Threading.AbandonedMutexException] { "
            "$mutex.ReleaseMutex(); exit 0 "
            "} "
            "} finally { $mutex.Dispose() }"
        )
        abandoned = subprocess.run(
            [powershell, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", abandoned_script],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        assert abandoned.returncode == 0, abandoned.stdout + abandoned.stderr
    finally:
        if child.poll() is None:
            child.terminate()
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait(timeout=5)
        if child.stdout:
            child.stdout.close()
        if child.stderr:
            child.stderr.close()
