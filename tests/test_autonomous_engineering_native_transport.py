from __future__ import annotations

import json
from pathlib import Path

import pytest

from app import autonomous_engineering_state_broker as broker
from app import autonomous_engineering_native_transport as native
from app import autonomous_engineering_native_config as config


@pytest.fixture
def native_root(monkeypatch, tmp_path):
    root = tmp_path / "native"
    monkeypatch.setenv("AUTONOMOUS_ENGINEERING_STATE_BACKEND", "native")
    monkeypatch.setattr(native, "execution_state_directory", lambda: root)
    monkeypatch.setattr(broker, "_open_pipe", lambda *a: pytest.fail("Native must not open a pipe"))
    return root


def test_native_health_is_read_only_until_explicit_setup(native_root):
    assert broker.health() is False
    assert not native_root.exists()
    assert native.setup_native_state()["ready"] is True
    assert broker.health(verify=True) is True
    assert not list(native_root.glob(".state-broker-probe-*"))
    assert broker.pending_work()["available"] is True


def test_native_transport_preserves_durable_state_during_cleanup(native_root):
    native.setup_native_state()
    (native_root / "current.json").write_text('{"status":"completed"}', encoding="utf-8")
    (native_root / "events.jsonl").write_text('{"type":"progress_event"}\n', encoding="utf-8")
    (native_root / "current.json.tmp").write_text("temporary", encoding="utf-8")
    result = broker.cleanup()
    assert result["cleaned"]
    assert not (native_root / "current.json.tmp").exists()
    assert json.loads((native_root / "current.json").read_text(encoding="utf-8")) == {"status": "completed"}
    assert (native_root / "events.jsonl").is_file()


def test_native_transport_reservation_is_fenced(native_root):
    native.setup_native_state()
    owner = "a" * 32
    assert broker.request("POST", "/v1/dispatch-admission/acquire", body={"request_id": owner})["acquired"]
    with pytest.raises(broker.StateBrokerConflict):
        broker.request("POST", "/v1/dispatch-admission/acquire", body={"request_id": "b" * 32})
    with pytest.raises(broker.StateBrokerConflict):
        broker.request("POST", "/v1/dispatch-admission/complete", body={"request_id": "b" * 32, "accepted": True})
    assert broker.request("POST", "/v1/dispatch-admission/complete", body={"request_id": owner, "accepted": True})["completed"]


def test_native_config_refuses_to_abandon_legacy_state(monkeypatch, tmp_path):
    import platformdirs
    legacy = tmp_path / "machine" / "PersonalAIAssistant" / "autonomous-engineering"
    legacy.mkdir(parents=True)
    (legacy / "pending-manual-task.txt").write_text("preserve", encoding="utf-8")
    user_data = tmp_path / "user"
    monkeypatch.setattr(platformdirs, "user_data_path", lambda *a, **k: user_data)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "home"))
    monkeypatch.setenv("PROGRAMDATA", str(tmp_path / "machine"))
    monkeypatch.delenv("AUTONOMOUS_ENGINEERING_PROGRESS_DIR", raising=False)
    monkeypatch.delenv("AUTONOMOUS_ENGINEERING_REQUIRE_MACHINE_STATE_DIR", raising=False)
    with pytest.raises(RuntimeError, match="legacy state"):
        config.resolve_native_state_directory(is_windows=True, machine_state=lambda: "")
    assert not user_data.exists()
    assert (legacy / "pending-manual-task.txt").read_text(encoding="utf-8") == "preserve"


def test_native_verify_checks_actual_write_access(native_root, monkeypatch):
    native.setup_native_state()
    def fail(_self):
        raise PermissionError("write denied")
    monkeypatch.setattr(native.RestrictedStateBroker, "health", fail)
    assert broker.health() is True
    assert broker.health(verify=True) is False
