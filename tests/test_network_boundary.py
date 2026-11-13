from fastapi.testclient import TestClient

from app.ai_learning import (
    browser_automation,
    config as ai_learning_config,
    routes as ai_learning_routes,
)
from app.main import app


def test_non_loopback_peers_cannot_access_ui_or_api():
    client = TestClient(app, client=("192.0.2.44", 54321))
    for path in ("/", "/api/health", "/api/settings", "/api/learning/jobs", "/api/autonomous-engineering/status"):
        response = client.get(path)
        assert response.status_code == 403, path
        assert response.json()["detail"] == "This application is available from localhost only."


def test_loopback_and_in_process_test_clients_remain_usable():
    loopback = TestClient(
        app, client=("127.0.0.1", 54321), base_url="http://127.0.0.1"
    )
    assert loopback.get("/").status_code == 200

    # The in-process ASGI test transport uses a synthetic peer name and Host;
    # real network clients cannot choose request.client.host.
    test_client = TestClient(app)
    assert test_client.get("/").status_code == 200


def test_dns_rebinding_and_nonlocal_host_headers_cannot_access_local_app():
    client = TestClient(app, client=("127.0.0.1", 54321))
    for host in (
        "attacker.example",
        "localhost.evil",
        "127.0.0.1.attacker.example",
        "localhost.",
    ):
        response = client.get("/api/health", headers={"host": host})
        assert response.status_code == 403, host
        assert response.json()["detail"] == "This application is available from localhost only."


def test_supported_loopback_host_headers_remain_usable():
    client = TestClient(app, client=("127.0.0.1", 54321))
    for host in ("127.0.0.1:8000", "localhost:8000", "[::1]:8000"):
        response = client.get("/api/health", headers={"host": host})
        assert response.status_code == 200, host



def test_forwarded_ip_headers_cannot_bypass_peer_boundary():
    client = TestClient(
        app,
        client=("192.0.2.44", 54321),
        headers={
            "X-Forwarded-For": "127.0.0.1",
            "X-Real-IP": "127.0.0.1",
        },
    )
    response = client.get("/api/health")
    assert response.status_code == 403


def test_non_loopback_ipv6_peer_is_rejected():
    client = TestClient(app, client=("2001:db8::44", 54321))
    response = client.get("/api/health")
    assert response.status_code == 403


def test_cross_origin_requests_cannot_reset_learning_data(monkeypatch):
    monkeypatch.setattr(ai_learning_config, "is_enabled", lambda: True)
    monkeypatch.setattr(ai_learning_routes, "initialize_store", lambda: None)
    reset_calls = []
    monkeypatch.setattr(
        browser_automation,
        "reset_learning_data",
        lambda: reset_calls.append(True) or {"reset": True},
    )
    client = TestClient(
        app,
        client=("127.0.0.1", 54321),
        base_url="http://127.0.0.1:8000",
    )
    hostile_headers = (
        {"origin": "https://attacker.example"},
        {"origin": "http://evil.localhost:8000", "sec-fetch-site": "same-site"},
        {"sec-fetch-site": "cross-site"},
        {"origin": "null"},
        {"origin": "http://127.0.0.1:0"},
        {"origin": "http://127.0.0.1:8000/attacker"},
        {"origin": "http://127.0.0.1:8000/"},
        {"origin": "http://user@127.0.0.1:8000"},
        {"origin": "http://127.0.0.1:99999"},
    )

    for headers in hostile_headers:
        response = client.post(
            "/api/ai-learning/reset-learning-data",
            headers=headers,
            data={},
        )
        assert response.status_code == 403
        assert response.json()["code"] == "cross_origin_api_request"

    assert reset_calls == []


def test_same_origin_browser_and_local_nonbrowser_api_calls_remain_usable(monkeypatch):
    monkeypatch.setattr(ai_learning_config, "is_enabled", lambda: True)
    monkeypatch.setattr(ai_learning_routes, "initialize_store", lambda: None)
    pause_calls = []
    monkeypatch.setattr(
        browser_automation,
        "pause_learning",
        lambda: pause_calls.append(True) or {"status": "paused"},
    )
    client = TestClient(
        app,
        client=("127.0.0.1", 54321),
        base_url="http://127.0.0.1:8000",
    )

    browser_response = client.post(
        "/api/ai-learning/pause",
        headers={
            "origin": "http://127.0.0.1:8000",
            "sec-fetch-site": "same-origin",
        },
    )
    assert browser_response.status_code == 200
    assert browser_response.json()["status"] == "paused"

    # Local CLI/test clients without browser metadata remain supported.
    cli_response = client.post("/api/ai-learning/pause")
    assert cli_response.status_code == 200
    assert len(pause_calls) == 2


def test_local_dashboard_responses_cannot_be_framed():
    client = TestClient(
        app,
        client=("127.0.0.1", 54321),
        base_url="http://127.0.0.1:8000",
    )
    response = client.get("/")

    assert response.status_code == 200
    assert response.headers["x-frame-options"] == "DENY"
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]


def test_cross_site_api_reads_are_blocked_but_page_navigation_is_allowed():
    client = TestClient(
        app,
        client=("127.0.0.1", 54321),
        base_url="http://127.0.0.1:8000",
    )
    api_response = client.get(
        "/api/ai-learning/status",
        headers={"sec-fetch-site": "cross-site"},
    )
    assert api_response.status_code == 403
    assert api_response.json()["code"] == "cross_origin_api_request"

    # A user following a link to the local dashboard is a navigation, not an API call.
    page_response = client.get("/", headers={"sec-fetch-site": "cross-site"})
    assert page_response.status_code == 200



def test_cross_origin_requests_cannot_dispatch_or_cancel_autonomous_agent(monkeypatch):
    from app import autonomous_engineering

    calls = []
    monkeypatch.setattr(autonomous_engineering, "dispatch_autonomous_engineering", lambda: calls.append("dispatch"))
    monkeypatch.setattr(autonomous_engineering, "cancel_autonomous_engineering", lambda: calls.append("cancel"))
    client = TestClient(
        app,
        client=("127.0.0.1", 54321),
        base_url="http://127.0.0.1:8000",
    )
    for path in ("/api/autonomous-engineering/dispatch", "/api/autonomous-engineering/cancel"):
        response = client.post(path, headers={
            "origin": "https://attacker.example",
            "sec-fetch-site": "cross-site",
        })
        assert response.status_code == 403
        assert response.json()["code"] == "cross_origin_api_request"
    assert calls == []



def test_every_registered_api_route_rejects_non_loopback_and_cross_origin_requests():
    """Apply the global local-only boundary to every current and future API route."""
    api_routes = [
        route
        for route in app.routes
        if isinstance(getattr(route, "path", None), str)
        and route.path.startswith("/api/")
        and getattr(route, "methods", None)
    ]
    assert api_routes, "The API route inventory must not be empty."

    local_client = TestClient(
        app,
        client=("127.0.0.1", 54321),
        base_url="http://127.0.0.1:8000",
    )
    remote_client = TestClient(
        app,
        client=("192.0.2.44", 54321),
        base_url="http://127.0.0.1:8000",
    )

    checked = set()
    for route in api_routes:
        for method in sorted(route.methods):
            if method not in {"GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"}:
                continue
            key = (method, route.path)
            if key in checked:
                continue
            checked.add(key)

            cross_origin = local_client.request(
                method,
                route.path,
                headers={
                    "origin": "https://attacker.example",
                    "sec-fetch-site": "cross-site",
                },
            )
            assert cross_origin.status_code == 403, key
            assert cross_origin.json()["code"] == "cross_origin_api_request", key

            remote = remote_client.request(method, route.path)
            assert remote.status_code == 403, key
            assert remote.json()["detail"] == "This application is available from localhost only.", key

    assert checked, "At least one concrete API method must be covered."
