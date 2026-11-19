from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "app" / "static"


def test_engineering_assistance_ui_uses_its_isolated_module_and_versioned_core():
    page = (STATIC / "index.html").read_text(encoding="utf-8")
    module = (STATIC / "engineering-assistance.js").read_text(encoding="utf-8")
    core = (STATIC / "dashboard-core.js").read_text(encoding="utf-8")

    assert 'src="/static/engineering-assistance.js"' in page
    assert "async function refreshSelfImprovement()" not in page
    assert 'el("programming-assist-form").addEventListener' not in page
    assert 'el("self-improvement-form").addEventListener' not in page

    for element_id in (
        "programming-assist-form",
        "programming-assist-submit",
        "programming-assist-status",
        "programming-assist-output",
        "self-improvement-form",
        "self-improvement-title",
        "self-improvement-rationale",
        "self-improvement-patch",
        "self-improvement-output",
        "self-improvement-refresh",
        "self-improvement-list",
    ):
        assert f'id="{element_id}"' in page

    for contract in (
        '"/api/programming/tasks"',
        '"/api/tasks/"+taskId',
        '"/api/self-improvement/proposals"',
        '"/api/self-improvement/proposals/"+item.id+"/events"',
        '"/api/self-improvement/proposals/"+item.id+"/validate"',
        '"/api/self-improvement/proposals/"+item.id+"/static-check"',
        '"/api/self-improvement/proposals/"+item.id+"/approve"',
        '"/api/self-improvement/proposals/"+item.id+"/open-pr"',
        '"/api/self-improvement/proposals/"+item.id+"/rollback-pr"',
        '"/api/self-improvement/proposals/"+item.id+"/reject"',
        "core.version!==1",
        "const {api,el}=core;",
        "window.confirm(",
        "candidate_code_executed",
        "refreshSelfImprovement();",
    ):
        assert contract in module

    assert "innerHTML" not in module
    assert "node.textContent = text;" in core


def test_engineering_assistance_controls_keep_existing_non_execution_guards():
    module = (STATIC / "engineering-assistance.js").read_text(encoding="utf-8")

    assert "کد اجرا نشده و هیچ آزمونی اجرا نشده است." in module
    assert "این مرحله آزمون کد اجرا نمی‌کند." in module
    assert "هیچ فایلی تغییر نکرد." in module
    assert "PR ادغام یا مستقر نمی‌شود" in module
    assert "هیچ ادغام یا استقراری انجام نمی‌شود." in module
