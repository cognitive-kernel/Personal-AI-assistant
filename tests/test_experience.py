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
        _controlled_run=True,
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
            _controlled_run=True,
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



def test_retrieval_only_returns_verified_experiences_and_accepts_cross_language_variants(tmp_path, monkeypatch):
    setup_learning_db(tmp_path, monkeypatch)
    unverified = experience.create_experience("SQL index", "An index scan was unexpectedly slow.")
    verified = experience.create_experience(
        "SQL Server index seek",
        "A query plan chose a scan for a selective predicate.",
        "Compare statistics and logical reads before adding an index.",
    )
    experience.update_experience(
        verified["id"],
        status="verified",
        source_evidence=[{
            "url": "https://learn.microsoft.com/en-us/sql/relational-databases/",
            "claim": "Microsoft documents query plans and indexes.",
            "original_language": "en",
        }],
        test_plan=[{"test": "compare logical reads on representative data"}],
        test_results=[{"test": "compare logical reads on representative data", "passed": True}],
        takeaway="Check statistics and actual plan before changing indexes.",
        _controlled_run=True,
    )

    results = experience.search_relevant_experiences(
        "ایندکس و طرح اجرای پرس‌وجو",
        query_variants=["SQL Server index seek query plan statistics"],
    )
    assert [item["id"] for item in results] == [verified["id"]]
    assert all(item["id"] != unverified["id"] for item in results)


def test_retrieval_returns_no_results_for_unrelated_query(tmp_path, monkeypatch):
    setup_learning_db(tmp_path, monkeypatch)
    item = experience.create_experience("Python JSON parser", "Parser rejected an empty payload.")
    experience.update_experience(
        item["id"],
        status="verified",
        source_evidence=[{"url": "https://docs.python.org/3/library/json.html", "claim": "JSON parser behavior"}],
        test_plan=[{"test": "empty payload is rejected"}],
        test_results=[{"test": "empty payload is rejected", "passed": True}],
        takeaway="Validate payloads before parsing.",
        _controlled_run=True,
    )
    assert experience.search_relevant_experiences("SQL Server query optimizer") == []



def test_public_update_cannot_promote_record_to_verified(tmp_path, monkeypatch):
    setup_learning_db(tmp_path, monkeypatch)
    item = experience.create_experience("Python", "Need to verify a parser behavior.")
    with pytest.raises(ValueError, match="controlled experience runner"):
        experience.update_experience(
            item["id"],
            status="verified",
            source_evidence=[{"url": "https://docs.python.org/3/library/json.html", "claim": "source"}],
            test_plan=[{"test": "contract"}],
            test_results=[{"test": "contract", "passed": True}],
        )
