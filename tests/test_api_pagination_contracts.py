import pytest
from fastapi.testclient import TestClient

from app import cloud_resources, db
from app.main import app


def _resolve_openapi_schema(schema: dict, document: dict) -> dict:
    """Resolve local OpenAPI schema references before asserting constraints."""
    resolved = dict(schema)
    visited = set()
    while "$ref" in resolved:
        reference = resolved.pop("$ref")
        if not isinstance(reference, str) or not reference.startswith("#/"):
            raise AssertionError(f"Unsupported OpenAPI schema reference: {reference!r}")
        if reference in visited:
            raise AssertionError(f"Circular OpenAPI schema reference: {reference}")
        visited.add(reference)
        target = document
        for segment in reference[2:].split("/"):
            segment = segment.replace("~1", "/").replace("~0", "~")
            target = target[segment]
        if not isinstance(target, dict):
            raise AssertionError(f"OpenAPI schema reference is not an object: {reference}")
        resolved = {**target, **resolved}
    return resolved


@pytest.fixture
def local_api_client(tmp_path, monkeypatch):
    monkeypatch.setattr(db.defaults, "settings_db_path", str(tmp_path / "settings.db"))
    monkeypatch.setattr(db.defaults, "learning_db_path", str(tmp_path / "learning.db"))
    monkeypatch.setattr(
        db.defaults, "cloud_resources_db_path", str(tmp_path / "cloud_resources.db")
    )
    monkeypatch.setattr(
        cloud_resources,
        "ensure_daily_discovery_scheduled",
        lambda: {"status": "disabled_for_test"},
    )
    with TestClient(app, client=("127.0.0.1", 12345)) as client:
        yield client


@pytest.mark.parametrize(
    ("path", "maximum"),
    [
        ("/api/learning/paths/1/curriculum-proposals", 100),
        ("/api/learning/capabilities", 500),
        ("/api/learning/jobs", 200),
        ("/api/knowledge", 300),
    ],
)
def test_list_endpoints_reject_out_of_range_limits(local_api_client, path, maximum):
    for invalid_limit in (0, maximum + 1):
        response = local_api_client.get(path, params={"limit": invalid_limit})
        assert response.status_code == 422, (path, invalid_limit)


def test_capability_map_rejects_non_positive_path_id_at_http_boundary(local_api_client):
    response = local_api_client.get("/api/learning/capabilities", params={"path_id": 0})
    assert response.status_code == 422


def test_pagination_bounds_are_published_in_openapi_schema():
    schema = app.openapi()
    expected = {
        ("/api/learning/paths/{path_id}/curriculum-proposals", "get", "limit"): (1, 100),
        ("/api/learning/capabilities", "get", "limit"): (1, 500),
        ("/api/learning/capabilities", "get", "path_id"): (1, None),
        ("/api/learning/jobs", "get", "limit"): (1, 200),
        ("/api/knowledge", "get", "limit"): (1, 300),
    }
    for (path, method, name), (minimum, maximum) in expected.items():
        parameters = schema["paths"][path][method]["parameters"]
        parameter = next(item for item in parameters if item["name"] == name)
        bounds = _resolve_openapi_schema(parameter["schema"], schema)
        assert bounds["minimum"] == minimum
        if maximum is not None:
            assert bounds["maximum"] == maximum


def test_every_api_limit_parameter_has_explicit_bounded_openapi_constraints():
    schema = app.openapi()
    inspected = 0
    for path, path_item in schema["paths"].items():
        if not path.startswith("/api/"):
            continue
        for method, operation in path_item.items():
            if method not in {"get", "post", "put", "patch", "delete"}:
                continue
            for parameter in operation.get("parameters", []):
                if parameter.get("name") != "limit":
                    continue
                inspected += 1
                bounds = _resolve_openapi_schema(parameter["schema"], schema)
                assert bounds.get("minimum", 0) >= 1, (method, path, parameter)
                assert 1 <= bounds.get("maximum", 0) <= 500, (method, path, parameter)
    assert inspected >= 10
