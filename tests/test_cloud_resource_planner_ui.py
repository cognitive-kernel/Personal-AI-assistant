from pathlib import Path


def test_cloud_resource_planner_ui_exposes_requirements_and_safe_dry_run_endpoint():
    root = Path(__file__).parents[1] / "app" / "static"
    html = (root / "index.html").read_text(encoding="utf-8")
    module = (root / "cloud-resources.js").read_text(encoding="utf-8")
    source = html + "\\n" + module

    for element_id in (
        "cloud-plan-section",
        "plan-min-cpu",
        "plan-min-memory",
        "plan-require-gpu",
        "plan-gpu-models",
        "plan-provider-domains",
        "cloud-plan-run",
        "cloud-plan-output",
    ):
        assert f'id="{element_id}"' in html

    assert 'api("/api/cloud-resources/plan"' in source
    assert "execution_authorized" in source
    assert "هیچ وظیفه‌ای اجرا نمی‌کند" in html
