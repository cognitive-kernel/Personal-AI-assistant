from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "app" / "static"


def test_dashboard_core_is_loaded_before_inline_and_feature_scripts():
    page = (STATIC / "index.html").read_text(encoding="utf-8")
    core = (STATIC / "dashboard-core.js").read_text(encoding="utf-8")

    core_tag = page.index('src="/static/dashboard-core.js"')
    inline_script = page.index("<script>", core_tag)
    cloud_tag = page.index('src="/static/cloud-resources.js"')
    sandbox_tag = page.index('src="/static/sandbox.js"')

    assert core_tag < inline_script < cloud_tag < sandbox_tag
    assert "const { el, api, appendText } = window.dashboardCore;" in page
    assert "window.dashboardCore = Object.freeze({" in core
    assert "version: 1" in core


def test_sandbox_feature_has_an_isolated_versioned_module_and_startup_refresh():
    page = (STATIC / "index.html").read_text(encoding="utf-8")
    module = (STATIC / "sandbox.js").read_text(encoding="utf-8")

    assert 'src="/static/sandbox.js"' in page
    assert "let activeSandboxTaskId" not in page
    assert "core.version!==1" in module
    assert "const {api,el}=core;" in module
    assert "async function refreshSandbox()" in module
    assert module.rstrip().endswith("refreshSandbox();\n})();")
    assert "innerHTML" not in module
