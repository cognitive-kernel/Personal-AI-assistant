from fastapi.testclient import TestClient

from app import main


def test_favicon_is_served_as_a_valid_ico(monkeypatch):
    # Avoid starting database workers: this endpoint only serves a static asset.
    monkeypatch.setattr(main, "startup", lambda: None)
    monkeypatch.setattr(main, "shutdown", lambda: None)

    with TestClient(main.app) as client:
        response = client.get("/favicon.ico")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/vnd.microsoft.icon")
    assert response.content[:4] == b"\x00\x00\x01\x00"
    assert len(response.content) > 22
