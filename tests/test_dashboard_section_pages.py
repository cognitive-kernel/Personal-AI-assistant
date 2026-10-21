from fastapi.testclient import TestClient

from app.main import DASHBOARD_SECTIONS, app


def test_each_dashboard_section_has_a_dedicated_route():
    client = TestClient(app)
    for slug, section_id in DASHBOARD_SECTIONS.items():
        response = client.get(f"/sections/{slug}")
        assert response.status_code == 200
        assert f'id="{section_id}"' in response.text
        assert f'data-section-slug="{slug}"' in response.text
        assert f'data-section-page="{slug}"' in response.text
        assert f'not(#{section_id})' in response.text


def test_dashboard_navigation_uses_dedicated_section_routes():
    client = TestClient(app)
    response = client.get("/")
    assert response.status_code == 200
    for slug in DASHBOARD_SECTIONS:
        assert f'href="/sections/{slug}"' in response.text


def test_unknown_dashboard_section_returns_404():
    response = TestClient(app).get("/sections/not-a-real-section")
    assert response.status_code == 404
