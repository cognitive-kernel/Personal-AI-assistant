from app import autonomous_engineering_progress as progress
from app import autonomous_engineering_state_broker as broker


def test_pending_work_status_exposes_bounded_state_broker_diagnostic(monkeypatch):
    def unavailable():
        raise broker.StateBrokerError("Windows state broker is unavailable (WinError 2).")

    monkeypatch.setattr(broker, "pending_work", unavailable)

    status = progress.pending_work_status()

    assert status["available"] is False
    assert status["blocked"] is True
    assert status["reason"] == (
        "Durable task state cannot be verified safely: "
        "Windows state broker is unavailable (WinError 2)."
    )


def test_pending_work_status_does_not_expose_arbitrary_exception_text(monkeypatch):
    def unexpected_failure():
        raise RuntimeError("private path C:\\Users\\example\\secret")

    monkeypatch.setattr(broker, "pending_work", unexpected_failure)

    status = progress.pending_work_status()

    assert status["reason"] == "Durable task state cannot be verified safely: RuntimeError"
    assert "secret" not in status["reason"]
