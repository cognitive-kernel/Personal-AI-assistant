from fastapi.testclient import TestClient

from app.main import app


def test_non_loopback_peers_cannot_access_ui_or_api():
    client = TestClient(app, client=("192.0.2.44", 54321))
    for path in ("/", "/api/health", "/api/settings", "/api/learning/jobs"):
        response = client.get(path)
        assert response.status_code == 403, path
        assert response.json()["detail"] == "This application is available from localhost only."


def test_loopback_and_in_process_test_clients_remain_usable():
    loopback = TestClient(app, client=("127.0.0.1", 54321))
    assert loopback.get("/").status_code == 200

    # The in-process ASGI test transport uses a synthetic peer name rather than
    # a socket IP; real network clients cannot choose request.client.host.
    test_client = TestClient(app)
    assert test_client.get("/").status_code == 200



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
