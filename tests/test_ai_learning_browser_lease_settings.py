from app.ai_learning import browser_automation


def test_browser_worker_lease_claim_uses_settings_snapshot(monkeypatch):
    settings = {
        "ai_learning_browser_lease_seconds": 120,
        "ai_learning_browser_lease_heartbeat_seconds": 20,
    }
    captured = {}
    monkeypatch.setattr(
        browser_automation,
        "_configured_seconds",
        lambda name, default, minimum, maximum: settings[name],
    )

    def claim(owner, *, lease_seconds):
        captured.update(owner=owner, lease_seconds=lease_seconds)
        return 17

    monkeypatch.setattr(browser_automation.service, "claim_worker_lease", claim)
    epoch = browser_automation._claim_configured_worker_lease()

    assert epoch == 17
    assert captured["lease_seconds"] == 120
    assert browser_automation._WORKER_LEASE_SECONDS == 120
    assert browser_automation._WORKER_LEASE_HEARTBEAT_SECONDS == 20.0


def test_browser_worker_lease_rejects_unsafe_heartbeat_ratio(monkeypatch):
    settings = {
        "ai_learning_browser_lease_seconds": 30,
        "ai_learning_browser_lease_heartbeat_seconds": 15,
    }
    monkeypatch.setattr(
        browser_automation,
        "_configured_seconds",
        lambda name, default, minimum, maximum: settings[name],
    )
    monkeypatch.setattr(
        browser_automation.service,
        "claim_worker_lease",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("lease must not be claimed")),
    )

    try:
        browser_automation._claim_configured_worker_lease()
    except RuntimeError as exc:
        assert "one third" in str(exc)
    else:
        raise AssertionError("unsafe heartbeat/lease ratio was accepted")
