from pathlib import Path


STATIC = Path(__file__).resolve().parents[1] / "app" / "static"


def test_experience_memory_ui_exposes_safe_curation_search_and_history():
    page = (STATIC / "index.html").read_text(encoding="utf-8")
    module = (STATIC / "experience-memory.js").read_text(encoding="utf-8")
    core = (STATIC / "dashboard-core.js").read_text(encoding="utf-8")

    assert 'src="/static/experience-memory.js"' in page
    for element_id in (
        "experience-memory-section",
        "experience-create-form",
        "experience-refresh",
        "experience-search-form",
        "experience-auto-capture-section",
        "experience-auto-capture-confirm",
        "experience-auto-capture-enable",
        "experience-auto-capture-revoke",
        "experience-auto-capture-refresh",
        "experience-list",
    ):
        assert f'id="{element_id}"' in page

    for contract in (
        '"/api/experiences/auto-capture/consent"',
        '"/api/experiences?limit=100"',
        '"/api/experiences/search?query="+encodeURIComponent(query)',
        '"/api/experiences/"+item.id+"/versions"',
        '"/api/experiences/"+item.id+"/run-test"',
        'source_code:sourceInput.value',
        'source_evidence:sourceEvidence,test_plan:testPlan,takeaway:takeawayInput.value',
        'link.rel="noopener noreferrer"',
        'source.protocol!=="https:"',
        'item.status==="verified"',
        "حداقل یک assert داشته باشد",
        "نتایج آزمون ثبت‌شده",
    ):
        assert contract in module

    assert "node.textContent = text;" in core
    assert "innerHTML" not in module
    assert "const {api,el,appendText}=core;" in module
    assert "core.version!==1" in module


def test_experience_feedback_ui_exposes_explicit_ranking_controls():
    module = (STATIC / "experience-memory.js").read_text(encoding="utf-8")

    assert "بازخورد برای بهبود رتبه‌بندی بازیابی" in module
    assert "مفید بود" in module
    assert "مفید نبود" in module
    assert "پاک‌کردن رأی" in module
    assert '"/api/experiences/"+item.id+"/feedback"' in module
    assert "changes_verification" not in module
