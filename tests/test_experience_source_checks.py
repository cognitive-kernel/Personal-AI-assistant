from datetime import datetime, timezone

import pytest

from app import db, experience, experience_source_checks, web_sources


def _setup(tmp_path, monkeypatch, urls=None):
    monkeypatch.setattr(db.defaults, "settings_db_path", str(tmp_path / "settings.db"))
    monkeypatch.setattr(db.defaults, "learning_db_path", str(tmp_path / "learning.db"))
    db.init_databases()
    item = experience.create_experience("Source reconciliation", "Check if saved public source text changed.")
    urls = urls or ["https://docs.example.org/guide"]
    evidence = [{"url": url, "claim": "Saved source evidence"} for url in urls]
    item = experience.update_experience(item["id"], source_evidence=evidence)
    return item


def test_source_revalidation_records_baseline_unchanged_and_changed_hashes(tmp_path, monkeypatch):
    item = _setup(tmp_path, monkeypatch)
    monkeypatch.setattr(experience_source_checks.web_sources, "_validate_public_url", lambda url: None)
    content = {"text": "A sufficiently long technical page body " * 5}

    def fetch(url, title_hint, *, max_chars=6000):
        return web_sources.SourceDocument(url=url, title="Guide", text=content["text"])

    monkeypatch.setattr(experience_source_checks.web_sources, "_fetch_document", fetch)
    first = experience_source_checks.refresh_experience_sources(item["id"])
    assert first["checks"][0]["outcome"] == "baseline"
    assert first["experience_mutated"] is False
    assert first["verified"] is False

    second = experience_source_checks.refresh_experience_sources(item["id"])
    assert second["checks"][0]["outcome"] == "unchanged"

    content["text"] = "A changed technical page body with new information " * 5
    third = experience_source_checks.refresh_experience_sources(item["id"])
    assert third["checks"][0]["outcome"] == "changed"
    assert third["checks"][0]["previous_content_sha256"] == second["checks"][0]["content_sha256"]
    assert len(experience_source_checks.list_source_checks(item["id"])) == 3
    assert experience.get_experience(item["id"])["version"] == item["version"]


def test_source_revalidation_fails_closed_for_policy_rejected_url(tmp_path, monkeypatch):
    item = _setup(tmp_path, monkeypatch)
    monkeypatch.setattr(
        experience_source_checks.web_sources,
        "_validate_public_url",
        lambda url: (_ for _ in ()).throw(web_sources.SourceFetchError("private destination")),
    )
    monkeypatch.setattr(
        experience_source_checks.web_sources,
        "_fetch_document",
        lambda *args, **kwargs: pytest.fail("blocked source must never be fetched"),
    )
    result = experience_source_checks.refresh_experience_sources(item["id"])
    assert result["checks"][0]["outcome"] == "blocked"
    assert result["checks"][0]["content_sha256"] is None


def test_source_revalidation_records_unavailable_without_leaking_fetch_error(tmp_path, monkeypatch):
    item = _setup(tmp_path, monkeypatch)
    monkeypatch.setattr(experience_source_checks.web_sources, "_validate_public_url", lambda url: None)
    monkeypatch.setattr(
        experience_source_checks.web_sources,
        "_fetch_document",
        lambda *args, **kwargs: (_ for _ in ()).throw(web_sources.SourceFetchError("sensitive diagnostic")),
    )
    result = experience_source_checks.refresh_experience_sources(item["id"])
    check = result["checks"][0]
    assert check["outcome"] == "unavailable"
    assert check["error_kind"] == "SourceFetchError"
    assert "sensitive diagnostic" not in str(check)


def test_source_revalidation_only_accepts_https_and_at_most_three_sources(tmp_path, monkeypatch):
    with pytest.raises(ValueError, match="HTTPS"):
        experience_source_checks._normalized_source_url("http://docs.example.org/guide")

    urls = [f"https://docs.example.org/page-{i}" for i in range(5)]
    item = _setup(tmp_path, monkeypatch, urls=urls)
    monkeypatch.setattr(experience_source_checks.web_sources, "_validate_public_url", lambda url: None)
    monkeypatch.setattr(
        experience_source_checks.web_sources,
        "_fetch_document",
        lambda url, title_hint, *, max_chars=6000: web_sources.SourceDocument(
            url=url, title=title_hint, text="A sufficiently long technical page body " * 5
        ),
    )
    result = experience_source_checks.refresh_experience_sources(item["id"])
    assert len(result["checks"]) == 3
