from pathlib import Path


def test_cloud_resource_ui_exposes_discovery_and_review_controls_safely():
    page = (Path(__file__).parents[1] / "app" / "static" / "index.html").read_text(
        encoding="utf-8"
    )
    module = (Path(__file__).parents[1] / "app" / "static" / "cloud-resources.js").read_text(
        encoding="utf-8"
    )
    source = page + "\\n" + module

    assert 'src="/static/cloud-resources.js"' in page
    assert "window.dashboardCore = Object.freeze({el, api, appendText})" in page
    assert "dashboardCore.{api,el,appendText}" in module

    assert 'id="cloud-resources-section"' in source
    assert 'id="cloud-refresh"' in source
    assert 'id="cloud-discover"' in source
    assert 'id="cloud-candidates"' in source
    assert 'id="cloud-candidate-limit"' in source
    assert 'id="cloud-manual-form"' in source
    assert 'api("/api/cloud-resources/manual"' in source
    assert 'method:"DELETE"' in source
    assert 'value="10" selected' in source
    assert 'api("/api/cloud-resources?limit="+candidateLimit)' in source
    assert '/api/cloud-resources/discover' in source
    assert '"/api/cloud-resources/"+candidate.id+"/review"' in source
    assert 'expected_content_sha256:candidate.content_sha256' in source
    assert 'link.rel="noopener noreferrer"' in source
    assert 'source.protocol!=="https:"' in source
    assert 'candidate.observed_claims||[]' in source
    assert 'document.createElement("details")' in source
    assert 'summary.textContent="جزئیات شواهد و ادعاها ("' in source
    assert 'card.append(details,deleteActions); container.append(card);' in source
    assert 'candidate.eligibility_assessment' in source
    assert 'candidate.source_authority==="recognized_official_domain"' in source
    assert 'دامنهٔ رسمی شناخته‌شده' in source
    assert 'execution_authorized' in source
    assert 'claim.evidence_quote' in source
    assert '"/claims/"+claim.id+"/review"' in source
    assert 'sendClaimReview("accepted")' in source
    assert 'sendClaimReview("rejected")' in source
    assert 'ادعاهای استخراج‌شده از متن (هنوز تأییدنشده)' in source
    # External source text must be rendered as text, never injected as markup.
    assert 'node.textContent=text' in source
    assert 'innerHTML' not in source
