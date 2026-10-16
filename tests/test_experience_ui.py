from pathlib import Path


def test_experience_memory_ui_exposes_safe_curation_search_and_history():
    page = (Path(__file__).parents[1] / "app" / "static" / "index.html").read_text(
        encoding="utf-8"
    )

    assert 'id="experience-memory-section"' in page
    assert 'id="experience-create-form"' in page
    assert 'id="experience-refresh"' in page
    assert 'id="experience-search-form"' in page
    assert 'id="experience-list"' in page
    assert '"/api/experiences?limit=100"' in page
    assert '"/api/experiences/search?query="+encodeURIComponent(query)' in page
    assert '"/api/experiences/"+item.id+"/versions"' in page
    assert '"/api/experiences/"+item.id' in page
    assert 'source_evidence:sourceEvidence,test_plan:testPlan,takeaway:takeawayInput.value' in page
    assert 'link.rel="noopener noreferrer"' in page
    assert 'source.protocol!=="https:"' in page
    assert 'item.status==="verified"' in page
    assert 'نتایج آزمون ثبت‌شده' in page
    # Database content and external source text must never be injected as HTML.
    assert 'node.textContent=text' in page
    assert 'innerHTML' not in page
