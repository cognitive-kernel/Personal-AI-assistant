from fastapi import APIRouter, FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import BaseModel, StrictStr

from app.autonomous_engineering import AutonomousEngineeringAPIRoute


class _RequestPayload(BaseModel):
    task: StrictStr


def _client_with_error_routes() -> TestClient:
    router = APIRouter(route_class=AutonomousEngineeringAPIRoute)

    @router.get("/conflict")
    def conflict():
        raise HTTPException(
            status_code=409,
            detail="A durable task is already pending.",
            headers={
                "X-Correlation-ID": "existing-correlation-821",
                "X-Error-Code": "PENDING_TASK_CONFLICT",
                "X-Retryable": "false",
            },
        )

    @router.post("/validate")
    def validate(payload: _RequestPayload):
        return {"task": payload.task}

    @router.get("/internal")
    def internal():
        raise RuntimeError("secret-token-must-not-reach-the-client")

    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_http_exception_uses_structured_error_contract_and_preserves_headers():
    response = _client_with_error_routes().get("/conflict")

    assert response.status_code == 409
    payload = response.json()
    assert payload["detail"] == "A durable task is already pending."
    assert payload["message"] == payload["detail"]
    assert payload["code"] == "PENDING_TASK_CONFLICT"
    assert payload["correlation_id"] == "existing-correlation-821"
    assert payload["retryable"] is False
    assert payload["safe_details"] == {}
    assert response.headers["X-Correlation-ID"] == payload["correlation_id"]
    assert response.headers["X-Error-Code"] == payload["code"]
    assert response.headers["X-Retryable"] == "false"


def test_request_validation_error_uses_safe_structured_details_without_echoing_input():
    response = _client_with_error_routes().post("/validate", json={"unexpected": "private-value"})

    assert response.status_code == 422
    payload = response.json()
    assert payload["code"] == "AUTONOMOUS_ENGINEERING_VALIDATION_FAILED"
    assert payload["message"] == "ورودی درخواست معتبر نیست."
    assert payload["retryable"] is False
    assert payload["correlation_id"]
    assert payload["safe_details"]["validation_errors"]
    assert "input" not in str(payload["safe_details"])
    assert "private-value" not in response.text
    assert response.headers["X-Correlation-ID"] == payload["correlation_id"]


def test_unexpected_exception_is_redacted_and_correlated():
    response = _client_with_error_routes().get("/internal")

    assert response.status_code == 500
    payload = response.json()
    assert payload["code"] == "AUTONOMOUS_ENGINEERING_INTERNAL_ERROR"
    assert payload["retryable"] is False
    assert payload["correlation_id"]
    assert payload["safe_details"] == {"exception_type": "RuntimeError"}
    assert "secret-token-must-not-reach-the-client" not in response.text
    assert response.headers["X-Correlation-ID"] == payload["correlation_id"]
