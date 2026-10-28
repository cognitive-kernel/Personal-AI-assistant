import pytest

from app import db, experience


def _setup(tmp_path, monkeypatch):
    monkeypatch.setattr(db.defaults, "settings_db_path", str(tmp_path / "settings.db"))
    monkeypatch.setattr(db.defaults, "learning_db_path", str(tmp_path / "learning.db"))
    monkeypatch.setattr(db.defaults, "cloud_resources_db_path", str(tmp_path / "cloud_resources.db"))
    db.init_databases()


def test_evolution_history_tracks_hypothesis_evidence_plan_and_test_changes(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    item = experience.create_experience(
        "Cache consistency",
        "Reads can return stale values.",
        "A cache invalidation race exists.",
    )
    experience.update_experience(
        item["id"],
        status="researching",
        source_evidence=[{
            "url": "https://docs.example.org/cache",
            "claim": "The cache invalidation contract.",
            "sha256": "a" * 64,
        }],
        test_plan=[{"test": "write then read", "expected": "new value"}],
        takeaway="Initial investigation.",
    )
    experience.update_experience(
        item["id"],
        status="failed",
        source_evidence=[
            {
                "url": "https://docs.example.org/cache",
                "claim": "The cache invalidation contract.",
                "sha256": "a" * 64,
            },
            {
                "url": "https://docs.example.org/transactions",
                "claim": "Commit visibility semantics.",
                "sha256": "b" * 64,
            },
        ],
        test_plan=[
            {"test": "write then read", "expected": "new value"},
            {"test": "concurrent writer", "expected": "serialized result"},
        ],
        test_results=[{"test": "write then read", "passed": False}],
        takeaway="The first hypothesis was not supported by the initial test.",
    )

    history = experience.list_experience_evolution(item["id"])

    assert history["current_version"] == 3
    assert history["version_count"] == 3
    assert len(history["changes"]) == 2
    second = history["changes"][1]
    assert second["status_transition"] == {"from": "researching", "to": "failed"}
    assert second["hypothesis_changed"] is False
    assert len(second["evidence_added"]) == 1
    assert second["evidence_added"][0]["url"].endswith("/transactions")
    assert second["test_plan_added"] == [
        {"test": "concurrent writer", "expected": "serialized result"}
    ]
    assert second["test_results_added_or_changed"] == [
        {"test": "write then read", "passed": False}
    ]
    assert "does not infer semantic equivalence" in history["interpretation_limit"]


def test_evolution_history_requires_an_existing_experience(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    with pytest.raises(KeyError):
        experience.list_experience_evolution(999)
