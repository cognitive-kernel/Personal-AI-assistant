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
    with pytest.raises(ValueError, match="controlled experience runner"):
        experience.update_experience(item["id"], status="verified")
    with pytest.raises(ValueError, match="source evidence"):
        experience.update_experience(item["id"], status="verified", _controlled_run=True)
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



def test_failed_experiences_are_retrieved_as_warnings_not_verified_guidance(tmp_path, monkeypatch):
    setup_learning_db(tmp_path, monkeypatch)
    item = experience.create_experience(
        "SQL Server index",
        "The lesson quality gate rejected a vague explanation of query plans.",
        "Add a concrete mechanism and measured trade-off.",
    )
    experience.update_experience(
        item["id"],
        status="needs_review",
        test_plan=[{"test": "learning_quality_gate"}],
        test_results=[{"test": "learning_quality_gate", "passed": False, "actual": "insufficient technical detail"}],
        takeaway="The earlier draft was rejected; do not reuse it as correct advice.",
    )
    results = experience.search_relevant_failures(
        "طرح اجرای پرس‌وجو و ایندکس",
        query_variants=["SQL Server query plan index"],
    )
    assert [row["id"] for row in results] == [item["id"]]
    assert results[0]["status"] == "needs_review"
    assert experience.search_relevant_experiences(
        "SQL Server query plan index",
    ) == []



def test_public_api_cannot_mutate_an_already_verified_record(tmp_path, monkeypatch):
    setup_learning_db(tmp_path, monkeypatch)
    item = experience.create_experience("Python", "A verified parser edge case.")
    experience.update_experience(
        item["id"],
        status="verified",
        source_evidence=[{"url": "https://docs.python.org/3/library/json.html", "claim": "source"}],
        test_plan=[{"test": "contract"}],
        test_results=[{"test": "contract", "passed": True}],
        takeaway="Original verified takeaway.",
        _controlled_run=True,
    )
    with pytest.raises(ValueError, match="controlled experience runner"):
        experience.update_experience(item["id"], takeaway="tampered")
    # A public caller must not bypass the guard by downgrading status first.
    with pytest.raises(ValueError, match="controlled experience runner"):
        experience.update_experience(
            item["id"],
            status="needs_review",
            takeaway="tampered after downgrade",
            source_evidence=[],
            test_plan=[],
            test_results=[],
        )
    unchanged = experience.get_experience(item["id"])
    assert unchanged["status"] == "verified"
    assert unchanged["takeaway"] == "Original verified takeaway."
    assert unchanged["version"] == 2


def test_experience_versions_can_be_inspected_and_controlled_rollback_is_a_new_version(tmp_path, monkeypatch):
    setup_learning_db(tmp_path, monkeypatch)
    item = experience.create_experience("Python", "A parser edge case.")
    experience.update_experience(item["id"], status="needs_review", takeaway="First recorded takeaway.")
    experience.update_experience(item["id"], status="failed", takeaway="Second recorded takeaway.")

    versions = experience.list_experience_versions(item["id"])
    assert {row["version"] for row in versions} == {1, 2, 3}
    with pytest.raises(ValueError, match="controlled experience runner"):
        experience.rollback_experience(item["id"], 2)

    restored = experience.rollback_experience(item["id"], 2, _controlled_run=True)
    assert restored["version"] == 4
    assert restored["status"] == "needs_review"
    assert restored["takeaway"] == "First recorded takeaway."
    assert {row["version"] for row in experience.list_experience_versions(item["id"])} == {1, 2, 3, 4}



def test_experience_retrieval_penalizes_stale_source_observations(tmp_path, monkeypatch):
    from datetime import datetime, timedelta, timezone
    from app import experience_retrieval

    setup_learning_db(tmp_path, monkeypatch)
    now = datetime.now(timezone.utc)
    recent = experience.create_experience("Python parser", "Parser behavior for empty JSON input.")
    stale = experience.create_experience("Python parser", "Parser behavior for empty JSON input.")
    for item, observed_at in (
        (recent, now.isoformat().replace("+00:00", "Z")),
        (stale, (now - timedelta(days=180)).isoformat().replace("+00:00", "Z")),
    ):
        experience.update_experience(
            item["id"],
            status="verified",
            source_evidence=[{
                "url": "https://docs.python.org/3/library/json.html",
                "claim": "Python JSON parser behavior",
                "observed_at": observed_at,
            }],
            test_plan=[{"test": "empty input contract"}],
            test_results=[{"test": "empty input contract", "passed": True}],
            takeaway="Validate empty JSON input.",
            _controlled_run=True,
        )
    monkeypatch.setattr(experience_retrieval, "_vector_ranking", lambda items, variants: [])
    results = experience.search_relevant_experiences("Python parser empty JSON input", limit=2)
    assert [row["id"] for row in results] == [recent["id"], stale["id"]]
    assert results[0]["source_freshness"]["status"] == "recently_observed"
    assert results[1]["source_freshness"]["status"] == "stale_or_unknown"


def test_experience_retrieval_treats_legacy_evidence_as_unknown_not_fresh(tmp_path, monkeypatch):
    from app import experience_retrieval

    setup_learning_db(tmp_path, monkeypatch)
    item = experience.create_experience("SQL query plan", "An optimizer chose a table scan.")
    experience.update_experience(
        item["id"],
        status="verified",
        source_evidence=[{
            "url": "https://learn.microsoft.com/en-us/sql/relational-databases/",
            "claim": "SQL Server query plans",
        }],
        test_plan=[{"test": "query plan contract"}],
        test_results=[{"test": "query plan contract", "passed": True}],
        takeaway="Inspect the actual execution plan.",
        _controlled_run=True,
    )
    freshness = experience_retrieval._source_freshness(item)
    assert freshness["status"] == "stale_or_unknown"
    assert freshness["multiplier"] == 0.8
