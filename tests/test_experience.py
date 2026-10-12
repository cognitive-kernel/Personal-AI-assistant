import pytest

from app import db, experience


def setup_learning_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db.defaults, "settings_db_path", str(tmp_path / "settings.db"))
    monkeypatch.setattr(db.defaults, "learning_db_path", str(tmp_path / "learning.db"))
    db.init_databases()
    experience.init_experience_tables()


def test_experience_persists_problem_sources_tests_and_takeaway(tmp_path, monkeypatch):
    setup_learning_db(tmp_path, monkeypatch)
    item = experience.create_experience("Python", "A parser failed on an empty input.", "Check the empty-input branch.")
    updated = experience.update_experience(
        item["id"],
        status="verified",
        source_evidence=[{"url": "https://docs.python.org/3/library/", "claim": "Documented behavior"}],
        test_plan=[{"test": "empty input returns an empty result"}],
        test_results=[{"test": "empty input returns an empty result", "passed": True, "actual": "empty result"}],
        takeaway="Handle empty input explicitly.",
    )
    assert updated["status"] == "verified"
    assert updated["version"] == 2
    assert updated["source_evidence"][0]["url"].startswith("https://")
    assert updated["test_results"][0]["passed"] is True
    assert experience.list_experiences(topic="Python", status="verified")[0]["id"] == item["id"]


def test_experience_cannot_be_verified_without_sources_and_passing_test(tmp_path, monkeypatch):
    setup_learning_db(tmp_path, monkeypatch)
    item = experience.create_experience("SQL", "Query returned an unexpected result.")
    with pytest.raises(ValueError, match="source evidence"):
        experience.update_experience(item["id"], status="verified")
    with pytest.raises(ValueError, match="passing test"):
        experience.update_experience(
            item["id"],
            status="verified",
            source_evidence=[{"url": "https://learn.microsoft.com/en-us/sql/", "claim": "Official docs"}],
            test_plan=[{"test": "regression test"}],
            test_results=[{"test": "regression test", "passed": False}],
        )


def test_experience_rejects_non_https_sources_and_invalid_test_result(tmp_path, monkeypatch):
    setup_learning_db(tmp_path, monkeypatch)
    item = experience.create_experience("Python", "Unexpected exception.")
    with pytest.raises(ValueError, match="HTTPS"):
        experience.update_experience(
            item["id"],
            source_evidence=[{"url": "http://example.com", "claim": "bad source"}],
        )
    with pytest.raises(ValueError, match="boolean passed"):
        experience.update_experience(
            item["id"],
            test_results=[{"test": "unit test", "passed": "yes"}],
        )
