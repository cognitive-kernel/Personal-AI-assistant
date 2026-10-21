from pathlib import Path


def test_cloud_resource_ui_exposes_discovery_and_review_controls_safely():
    app_root = Path(__file__).parents[1] / "app"
    page = (app_root / "static" / "index.html").read_text(encoding="utf-8") + "\n" + (app_root / "dashboard_sections" / "cloud-resources.html").read_text(encoding="utf-8")

    assert 'id="cloud-resources-section"' in page
    assert 'id="cloud-refresh"' in page
    assert 'id="cloud-discover"' in page
    assert 'id="cloud-candidates"' in page
    assert 'id="cloud-candidate-limit"' in page
    assert 'value="10" selected' in page
    assert 'api("/api/cloud-resources?limit="+candidateLimit)' in page
    assert '/api/cloud-resources/discover' in page
    assert '"/api/cloud-resources/"+candidate.id+"/review"' in page
    assert 'expected_content_sha256:candidate.content_sha256' in page
    assert 'link.rel="noopener noreferrer"' in page
    assert 'source.protocol!=="https:"' in page
    assert 'candidate.observed_claims||[]' in page
    assert 'document.createElement("details")' in page
    assert 'summary.textContent="جزئیات شواهد و ادعاها ("' in page
    assert 'details.append(form); card.append(details); container.append(card);' in page
    assert 'candidate.eligibility_assessment' in page
    assert 'candidate.source_authority==="recognized_official_domain"' in page
    assert 'دامنهٔ رسمی شناخته‌شده' in page
    assert 'execution_authorized' in page
    assert 'claim.evidence_quote' in page
    assert '"/claims/"+claim.id+"/review"' in page
    assert 'sendClaimReview("accepted")' in page
    assert 'sendClaimReview("rejected")' in page
    assert 'ادعاهای استخراج‌شده از متن (هنوز تأییدنشده)' in page
    # External source text must be rendered as text, never injected as markup.
    assert 'node.textContent=text' in page
    assert 'innerHTML' not in page
