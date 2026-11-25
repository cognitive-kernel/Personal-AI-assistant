from fastapi import HTTPException

from app import autonomous_engineering as agent


def test_broker_setup_route_is_loopback_protected_post():
    route = next(
        item for item in agent.router.routes
        if getattr(item, "path", None) == "/api/autonomous-engineering/broker/setup"
    )
    assert "POST" in route.methods
    assert agent.require_loopback_client in [
        dependency.dependency for dependency in agent.router.dependencies
    ]


def test_broker_setup_route_returns_verified_result(monkeypatch):
    expected = {"ready": True, "already_ready": False, "runtime": "Core"}
    monkeypatch.setattr(
        "app.autonomous_engineering_broker_setup.setup_state_broker",
        lambda: expected,
    )
    assert agent.autonomous_engineering_setup_broker() == expected


def test_broker_setup_route_preserves_structured_safe_failure(monkeypatch):
    from app.autonomous_engineering_broker_setup import BrokerSetupError

    def fail():
        raise BrokerSetupError(
            "UAC cancelled",
            code="STATE_BROKER_ELEVATION_CANCELLED",
            status_code=403,
            safe_details={"runtime": "Desktop"},
        )

    monkeypatch.setattr("app.autonomous_engineering_broker_setup.setup_state_broker", fail)
    try:
        agent.autonomous_engineering_setup_broker()
    except HTTPException as error:
        assert error.status_code == 403
        assert error.headers["X-Error-Code"] == "STATE_BROKER_ELEVATION_CANCELLED"
        assert error.headers["X-Retryable"] == "false"
        assert error.safe_details == {"runtime": "Desktop"}
    else:
        raise AssertionError("Expected BrokerSetupError to be translated to HTTPException.")
