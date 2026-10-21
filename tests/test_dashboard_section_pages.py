from pathlib import Path

from fastapi.testclient import TestClient

from app.main import DASHBOARD_SECTIONS, app



def test_dashboard_markup_is_physically_split_into_page_templates():
    project_root = Path(__file__).resolve().parents[1]
    shell = (project_root / "app" / "static" / "index.html").read_text(encoding="utf-8")
    assert 'id="dashboard-overview"' not in shell
    assert 'id="storage-degraded-banner"' in shell
    for slug, section_id in DASHBOARD_SECTIONS.items():
        assert f'id="{section_id}"' not in shell, (
            f"{section_id} must not be embedded in the shared main-page shell"
        )
        template = project_root / "app" / "dashboard_sections" / f"{slug}.html"
        assert template.is_file(), f"Missing dedicated page template for {slug}"
        assert f'id="{section_id}"' in template.read_text(encoding="utf-8")


def test_each_dashboard_section_returns_only_its_own_html_content():
    client = TestClient(app)
    for slug, section_id in DASHBOARD_SECTIONS.items():
        response = client.get(f"/sections/{slug}")
        assert response.status_code == 200
        assert f'id="{section_id}"' in response.text
        assert f'data-section-slug="{slug}"' in response.text
        assert f'data-section-page="{slug}"' in response.text
        for other_slug, other_id in DASHBOARD_SECTIONS.items():
            if other_slug != slug:
                assert f'id="{other_id}"' not in response.text, (
                    f"{slug} page leaked the {other_slug} section markup"
                )
        assert 'id="dashboard-overview"' not in response.text


def test_main_dashboard_is_an_overview_not_a_container_for_every_feature():
    response = TestClient(app).get("/")
    assert response.status_code == 200
    assert 'id="dashboard-overview"' in response.text
    for section_id in DASHBOARD_SECTIONS.values():
        assert f'id="{section_id}"' not in response.text
    for slug in DASHBOARD_SECTIONS:
        assert f'href="/sections/{slug}"' in response.text


def test_learning_page_keeps_evidence_review_but_excludes_unrelated_sections():
    response = TestClient(app).get("/sections/learning")
    assert response.status_code == 200
    assert 'id="learning-section"' in response.text
    assert 'id="learning-evidence-section"' in response.text
    assert 'id="task-manager-section"' not in response.text
    assert 'id="cloud-resources-section"' not in response.text


def test_unknown_dashboard_section_returns_404():
    response = TestClient(app).get("/sections/not-a-real-section")
    assert response.status_code == 404
