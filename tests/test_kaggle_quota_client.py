import subprocess
from datetime import datetime, timezone

import pytest

from app import kaggle_quota_client
from app.kaggle_quota import KaggleQuotaSnapshot
from app.kaggle_quota_client import KaggleQuotaClientError, fetch_quota_snapshot


VALID_CSV = """resource,used,remaining,total,refreshAt
GPU,1.00h,9.00h,10.00h,2099-10-17T00:00:00+00:00
TPU,0.00h,20.00h,20.00h,2099-10-17T00:00:00+00:00
"""


def _executable(tmp_path):
    executable = tmp_path / ("kaggle.exe" if __import__("os").name == "nt" else "kaggle")
    executable.write_text("placeholder", encoding="utf-8")
    return executable


@pytest.mark.parametrize(
    "enabled,user_consent",
    [(False, True), (True, False), (False, False), (1, True), (True, 1)],
)
def test_quota_client_requires_explicit_enablement_and_consent(
    tmp_path, monkeypatch, enabled, user_consent
):
    monkeypatch.setattr(
        kaggle_quota_client.subprocess,
        "run",
        lambda *args, **kwargs: pytest.fail("must not launch CLI without consent"),
    )
    with pytest.raises(KaggleQuotaClientError, match="explicitly enables"):
        fetch_quota_snapshot(
            executable_path=_executable(tmp_path),
            enabled=enabled,
            user_consent=user_consent,
        )


def test_quota_client_calls_only_fixed_read_only_command_and_parses_output(
    tmp_path, monkeypatch
):
    executable = _executable(tmp_path)
    calls = []

    def fake_run(args, **kwargs):
        calls.append((args, kwargs))
        return subprocess.CompletedProcess(args, 0, stdout=VALID_CSV, stderr="")

    monkeypatch.setattr(kaggle_quota_client.subprocess, "run", fake_run)
    before = datetime.now(timezone.utc)
    snapshot = fetch_quota_snapshot(
        executable_path=executable,
        enabled=True,
        user_consent=True,
    )
    after = datetime.now(timezone.utc)

    assert isinstance(snapshot, KaggleQuotaSnapshot)
    assert snapshot.gpu.remaining_hours == 9
    assert before <= snapshot.observed_at <= after
    assert len(calls) == 1
    args, kwargs = calls[0]
    assert args == [str(executable), "quota", "--csv"]
    assert kwargs["shell"] is False
    assert kwargs["capture_output"] is True
    assert kwargs["text"] is True
    assert kwargs["check"] is False
    assert kwargs["timeout"] == 10
    assert "env" in kwargs


def test_quota_client_does_not_echo_stderr_or_credentials_on_failure(tmp_path, monkeypatch):
    executable = _executable(tmp_path)
    monkeypatch.setattr(
        kaggle_quota_client.subprocess,
        "run",
        lambda args, **kwargs: subprocess.CompletedProcess(
            args, 1, stdout="", stderr="secret-token-should-not-appear"
        ),
    )

    with pytest.raises(KaggleQuotaClientError, match="exit code 1") as error:
        fetch_quota_snapshot(
            executable_path=executable,
            enabled=True,
            user_consent=True,
        )
    assert "secret-token" not in str(error.value)


def test_quota_client_handles_timeout_and_invalid_paths(tmp_path, monkeypatch):
    executable = _executable(tmp_path)

    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], kwargs["timeout"])

    monkeypatch.setattr(kaggle_quota_client.subprocess, "run", timeout)
    with pytest.raises(KaggleQuotaClientError, match="timed out"):
        fetch_quota_snapshot(
            executable_path=executable,
            enabled=True,
            user_consent=True,
        )

    with pytest.raises(KaggleQuotaClientError, match="absolute path"):
        fetch_quota_snapshot(
            executable_path="kaggle",
            enabled=True,
            user_consent=True,
        )


def test_quota_client_rejects_invalid_output_without_leaking_cli_details(tmp_path, monkeypatch):
    executable = _executable(tmp_path)
    monkeypatch.setattr(
        kaggle_quota_client.subprocess,
        "run",
        lambda args, **kwargs: subprocess.CompletedProcess(args, 0, stdout="bad", stderr=""),
    )

    with pytest.raises(KaggleQuotaClientError, match="invalid quota data"):
        fetch_quota_snapshot(
            executable_path=executable,
            enabled=True,
            user_consent=True,
        )


def test_quota_client_validates_timeout_bounds(tmp_path):
    with pytest.raises(ValueError, match="timeout_seconds"):
        fetch_quota_snapshot(
            executable_path=_executable(tmp_path),
            enabled=True,
            user_consent=True,
            timeout_seconds=True,
        )
