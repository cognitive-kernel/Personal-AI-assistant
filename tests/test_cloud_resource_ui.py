from pathlib import Path


def test_cloud_resource_ui_exposes_discovery_and_review_controls_safely():
    page = (Path(__file__).parents[1] / "app" / "static" / "index.html").read_text(
        encoding="utf-8"
    )

    assert 'id="cloud-resources-section"' in page
    assert 'id="cloud-refresh"' in page
    assert 'id="cloud-discover"' in page
    assert 'id="cloud-candidates"' in page
    assert '/api/cloud-resources/discover' in page
    assert '"/api/cloud-resources/"+candidate.id+"/review"' in page
    assert 'expected_content_sha256:candidate.content_sha256' in page
    assert 'link.rel="noopener noreferrer"' in page
    assert 'source.protocol!=="https:"' in page
    assert 'candidate.observed_claims||[]' in page
    assert 'claim.evidence_quote' in page
    assert '"/claims/"+claim.id+"/review"' in page
    assert 'sendClaimReview("accepted")' in page
    assert 'sendClaimReview("rejected")' in page
    assert 'ادعاهای استخراج‌شده از متن (هنوز تأییدنشده)' in page
    # External source text must be rendered as text, never injected as markup.
    assert 'node.textContent=text' in page
    assert 'innerHTML' not in page
