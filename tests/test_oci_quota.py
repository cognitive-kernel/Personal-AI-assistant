import json
import subprocess
from datetime import datetime, timedelta, timezone

import pytest

from app import oci_quota_client
from app.oci_quota import OciQuotaError, parse_limits_json
from app.oci_quota_client import OciQuotaClientError, fetch_service_limits


REGION = "us-ashburn-1"
COMPARTMENT = "ocid1.compartment.oc1..aaaaaaaaexample"
PAYLOAD = {
    "data": [
        {
            "name": "standard-a1-core-count",
            "value": 2,
            "used": 1,
            "available": 1,
            "scope-type": "REGION",
            "scope-value": REGION,
        },
        {
            "name": "standard-a1-memory-count",
            "value": 12,
            "used": 4,
            "available": 8,
            "scope-type": "REGION",
            "scope-value": REGION,
        },
    ]
}


def test_parse_oci_limits_json_and_freshness():
    observed = datetime(2026, 10, 16, 8, 0, tzinfo=timezone.utc)
    snapshot = parse_limits_json(
        json.dumps(PAYLOAD),
        observed_at=observed,
        region=REGION,
    )

    assert snapshot.region == REGION
    assert [item.name for item in snapshot.limits] == [
        "standard-a1-core-count",
        "standard-a1-memory-count",
    ]
    assert snapshot.limits[0].value == 2
    assert snapshot.limits[0].used == 1
    assert snapshot.limits[0].available == 1
    assert snapshot.is_fresh(now=observed + timedelta(minutes=4))
    assert not snapshot.is_fresh(now=observed + timedelta(minutes=6))


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"data": []},
        {"data": [{"name": "limit", "value": float("nan")}]},
        {"data": [{"name": "limit", "value": 1, "used": 2}]},
        {
            "data": [
                {"name": "limit", "value": 2, "scope-type": "REGION", "scope-value": REGION},
                {"name": "limit", "value": 2, "scope-type": "REGION", "scope-value": REGION},
            ]
        },
        {"data": ["not an object"]},
    ],
)
def test_parser_rejects_malformed_or_ambiguous_limits(payload):
    with pytest.raises(OciQuotaError):
        parse_limits_json(
            json.dumps(payload),
            observed_at=datetime.now(timezone.utc),
            region=REGION,
        )


def test_client_requires_explicit_enablement_and_consent_before_command(tmp_path, monkeypatch):
    executable = tmp_path / "oci"
    config = tmp_path / "config"
    executable.write_text("placeholder", encoding="utf-8")
    config.write_text("[DEFAULT]\n", encoding="utf-8")
    called = []
    monkeypatch.setattr(oci_quota_client.subprocess, "run", lambda *args, **kwargs: called.append(args))

    with pytest.raises(OciQuotaClientError, match="explicitly enabled"):
        fetch_service_limits(
            executable_path=executable,
            config_file=config,
            profile="DEFAULT",
            region=REGION,
            compartment_id=COMPARTMENT,
            enabled=True,
            user_consent=False,
        )
    assert called == []


def test_client_runs_only_fixed_read_only_compute_limits_command(tmp_path, monkeypatch):
    executable = tmp_path / "oci"
    config = tmp_path / "config"
    executable.write_text("placeholder", encoding="utf-8")
    config.write_text("[DEFAULT]\n", encoding="utf-8")
    captured = {}
    monkeypatch.setenv("OCI_PRIVATE_KEY", "must-not-be-forwarded")
    monkeypatch.setenv("KAGGLE_API_TOKEN", "must-not-be-forwarded")

    def fake_run(args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return subprocess.CompletedProcess(args, 0, json.dumps(PAYLOAD), "")

    monkeypatch.setattr(oci_quota_client.subprocess, "run", fake_run)
    snapshot = fetch_service_limits(
        executable_path=executable,
        config_file=config,
        profile="DEFAULT",
        region=REGION,
        compartment_id=COMPARTMENT,
        enabled=True,
        user_consent=True,
    )

    args = captured["args"]
    assert args[:7] == [
        str(executable), "--config-file", str(config), "--profile",
        "DEFAULT", "--region", REGION,
    ]
    assert args[args.index("limits") : args.index("limits") + 3] == [
        "limits", "value", "list"
    ]
    assert "--service-name" in args and args[args.index("--service-name") + 1] == "compute"
    assert "--output" in args and args[args.index("--output") + 1] == "json"
    assert captured["kwargs"]["shell"] is False
    assert captured["kwargs"]["timeout"] == 15
    assert "OCI_PRIVATE_KEY" not in captured["kwargs"]["env"]
    assert "KAGGLE_API_TOKEN" not in captured["kwargs"]["env"]
    assert len(snapshot.limits) == 2


def test_client_does_not_expose_cli_stderr(tmp_path, monkeypatch):
    executable = tmp_path / "oci"
    config = tmp_path / "config"
    executable.write_text("placeholder", encoding="utf-8")
    config.write_text("[DEFAULT]\n", encoding="utf-8")
    monkeypatch.setattr(
        oci_quota_client.subprocess,
        "run",
        lambda args, **kwargs: subprocess.CompletedProcess(args, 2, "", "sensitive account details"),
    )

    with pytest.raises(OciQuotaClientError) as exc:
        fetch_service_limits(
            executable_path=executable,
            config_file=config,
            profile="DEFAULT",
            region=REGION,
            compartment_id=COMPARTMENT,
            enabled=True,
            user_consent=True,
        )
    assert "sensitive account details" not in str(exc.value)


@pytest.mark.parametrize(
    "overrides",
    [
        {"profile": "bad profile"},
        {"region": "region; echo unsafe"},
        {"compartment_id": "--help"},
    ],
)
def test_client_rejects_cli_argument_injection_inputs(tmp_path, monkeypatch, overrides):
    executable = tmp_path / "oci"
    config = tmp_path / "config"
    executable.write_text("placeholder", encoding="utf-8")
    config.write_text("[DEFAULT]\n", encoding="utf-8")
    called = []
    monkeypatch.setattr(oci_quota_client.subprocess, "run", lambda *args, **kwargs: called.append(args))
    arguments = {
        "executable_path": executable,
        "config_file": config,
        "profile": "DEFAULT",
        "region": REGION,
        "compartment_id": COMPARTMENT,
        "enabled": True,
        "user_consent": True,
    }
    arguments.update(overrides)
    with pytest.raises(ValueError):
        fetch_service_limits(**arguments)
    assert called == []
