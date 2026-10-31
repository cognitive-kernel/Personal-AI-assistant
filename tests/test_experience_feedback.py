import pytest

from app import db, experience, experience_feedback


def _setup(tmp_path, monkeypatch):
    monkeypatch.setattr(db.defaults, "settings_db_path", str(tmp_path / "settings.db"))
    monkeypatch.setattr(db.defaults, "learning_db_path", str(tmp_path / "learning.db"))
    db.init_databases()


def _verified():
    item = experience.create_experience("Feedback topic", "A reproducible problem.")
    return experience.update_experience(
        item["id"],
        status="verified",
        source_evidence=[{"url": "https://docs.example.org/", "claim": "Official source"}],
        test_plan=[{"test": "regression"}],
        test_results=[{"test": "regression", "passed": True}],
        takeaway="A tested resolution.",
        _controlled_run=True,
    )


def test_feedback_is_explicit_replaceable_and_deletable(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    item = _verified()
    assert experience_feedback.get_feedback_map([item["id"]]) == {}

    first = experience_feedback.set_feedback(item["id"], 1)
    assert first["rating"] == 1
    second = experience_feedback.set_feedback(item["id"], -1)
    assert second["rating"] == -1
    assert experience_feedback.get_feedback_map([item["id"]])[item["id"]]["rating"] == -1

    assert experience_feedback.delete_feedback(item["id"]) is True
    assert experience_feedback.get_feedback_map([item["id"]]) == {}
    assert experience_feedback.delete_feedback(item["id"]) is False


def test_feedback_cannot_change_verification_or_be_added_to_unverified_record(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    item = experience.create_experience("Unverified", "Not yet tested.")
    with pytest.raises(ValueError, match="verified experience"):
        experience_feedback.set_feedback(item["id"], 1)
    with pytest.raises(ValueError, match="must be -1 or 1"):
        experience_feedback.set_feedback(item["id"], 0)
    assert experience.get_experience(item["id"])["status"] == "identified"



def test_feedback_requires_existing_experience(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    with pytest.raises(KeyError, match="999"):
        experience_feedback.set_feedback(999, 1)
    with pytest.raises(KeyError, match="999"):
        experience_feedback.delete_feedback(999)

def test_feedback_service_rejects_coercive_ratings_and_experience_ids(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    item = _verified()

    for rating in (True, 1.0, -1.0, "1", " -1 "):
        with pytest.raises(ValueError, match="integer -1 or 1"):
            experience_feedback.set_feedback(item["id"], rating)

    for experience_id in (True, 1.0, "1", 0, -1):
        with pytest.raises(ValueError, match="positive integer"):
            experience_feedback.set_feedback(experience_id, 1)
        with pytest.raises(ValueError, match="positive integer"):
            experience_feedback.delete_feedback(experience_id)

    with pytest.raises(ValueError, match="positive integers"):
        experience_feedback.get_feedback_map([True])
    with pytest.raises(ValueError, match="positive integers"):
        experience_feedback.get_feedback_map([item["id"], 1.0])
    assert experience_feedback.get_feedback_map([item["id"]]) == {}
