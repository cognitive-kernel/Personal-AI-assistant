from types import SimpleNamespace

import pytest

from app import db, factual_knowledge, factual_source_checks, web_sources


URL = "https://docs.python.org/3/library/stdtypes.html"


def _active_claim(tmp_path, monkeypatch):
    monkeypatch.setattr(db.defaults, "settings_db_path", str(tmp_path / "settings.db"))
    monkeypatch.setattr(db.defaults, "learning_db_path", str(tmp_path / "learning.db"))
    db.init_databases()
    with db.learning_connection() as conn:
        claim_id = factual_knowledge.create_claim(
            conn, "Python lists preserve insertion order", environment_version="3.12"
        )
        factual_knowledge.add_evidence(
            conn,
            claim_id,
            source_type="official_docs",
            source_ref=URL,
            source_hash="sha256:saved-source-v1",
            excerpt="Lists preserve insertion order.",
        )
        factual_knowledge.transition_evidence_level(conn, claim_id, "L1_sourced")
        factual_knowledge.record_test(
            conn,
            claim_id,
            test_kind="unit",
            test_name="list-order-regression",
            environment_version="3.12",
            outcome="passed",
            result_summary="Observed insertion order is preserved.",
        )
        factual_knowledge.transition_evidence_level(conn, claim_id, "L2_tested")
        factual_knowledge.transition_lifecycle(conn, claim_id, "active")
    return claim_id


def test_source_refresh_baselines_then_detects_changed_content(tmp_path, monkeypatch):
    claim_id = _active_claim(tmp_path, monkeypatch)
    document_text = {"value": "Lists preserve insertion order. Stable source content."}
    monkeypatch.setattr(web_sources, "_validate_public_url", lambda _url: None)
    monkeypatch.setattr(
        web_sources,
        "_fetch_document",
        lambda _url, _user_agent, max_chars: SimpleNamespace(
            title="Python sequence types", text=document_text["value"][:max_chars]
        ),
    )

    baseline = factual_source_checks.refresh_claim_sources(claim_id, confirm=True)
    assert baseline["counts"] == {"baseline": 1}
    assert baseline["needs_review"] is False
    assert baseline["verified"] is False

    unchanged = factual_source_checks.refresh_claim_sources(claim_id, confirm=True)
    assert unchanged["counts"] == {"unchanged": 1}
    assert unchanged["needs_review"] is False

    document_text["value"] = "Lists preserve insertion order. Stable source content changed."
    changed = factual_source_checks.refresh_claim_sources(claim_id, confirm=True)
    assert changed["counts"] == {"changed": 1}
    assert changed["needs_review"] is True
    assert changed["lifecycle_status"] == "needs_review"
    assert changed["evidence_level"] == "L2_tested"

    history = factual_source_checks.list_source_checks(claim_id, limit=10)
    assert [item["outcome"] for item in history] == ["changed", "unchanged", "baseline"]


def test_source_refresh_fails_closed_on_blocked_destination(tmp_path, monkeypatch):
    claim_id = _active_claim(tmp_path, monkeypatch)

    def reject(_url):
        raise ValueError("private or disallowed destination")

    monkeypatch.setattr(web_sources, "_validate_public_url", reject)
    result = factual_source_checks.refresh_claim_sources(claim_id, confirm=True)
    assert result["counts"] == {"blocked": 1}
    assert result["needs_review"] is True
    assert result["evidence_level"] == "L2_tested"
    assert factual_source_checks.list_source_checks(claim_id)[0]["error_kind"] == "SourcePolicyRejected"


@pytest.mark.parametrize(
    "url",
    [
        "http://docs.python.org/3/library/stdtypes.html",
        "https://user:pass@docs.python.org/3/library/stdtypes.html",
        "https://docs.python.org:8443/3/library/stdtypes.html",
    ],
)
def test_saved_source_url_policy_rejects_unsafe_urls(url):
    with pytest.raises(ValueError):
        factual_source_checks._normalized_https_url(url)


def test_refresh_requires_explicit_confirmation(tmp_path, monkeypatch):
    claim_id = _active_claim(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="Explicit confirmation"):
        factual_source_checks.refresh_claim_sources(claim_id, confirm=False)



def test_revalidation_history_does_not_collide_with_legacy_source_checks(tmp_path, monkeypatch):
    _active_claim(tmp_path, monkeypatch)
    with db.learning_connection() as conn:
        legacy_columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(factual_source_checks)")
        }
        history_columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(factual_source_revalidation_history)")
        }
    assert {"evidence_id", "claim_hash", "expected_source_hash"} <= legacy_columns
    assert {
        "knowledge_id", "source_ref", "outcome",
        "previous_content_sha256", "current_content_sha256",
        "evidence_excerpt_sha256", "error_kind",
    } <= history_columns
