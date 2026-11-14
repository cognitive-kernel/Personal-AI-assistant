import concurrent.futures

import pytest

from app import db
from app.learning_topic_method_attempts import (
    METHOD_ORDER,
    MethodAttemptError,
    append_attempt_event,
    latest_attempt_event,
    list_attempt_events,
    next_method,
)


@pytest.fixture
def method_attempt_store(tmp_path, monkeypatch):
    monkeypatch.setattr(db.defaults, "settings_db_path", str(tmp_path / "settings.db"))
    monkeypatch.setattr(db.defaults, "learning_db_path", str(tmp_path / "learning.db"))
    db.init_databases()
    with db.learning_connection() as connection:
        path_id = int(connection.execute(
            "INSERT INTO learning_paths(curriculum_slug, title) VALUES ('method-ledger', 'Method ledger')"
        ).lastrowid)
        topic_id = int(connection.execute(
            """INSERT INTO learning_path_topics(path_id, topic, position, status)
               VALUES (?, 'State machine', 1, 'in_progress')""",
            (path_id,),
        ).lastrowid)
    return topic_id


def test_method_order_is_fixed_and_exhaustive():
    assert METHOD_ORDER == ("ai_learning", "basic_learning", "topic_learning")
    assert next_method("ai_learning") == "basic_learning"
    assert next_method("basic_learning") == "topic_learning"
    assert next_method("topic_learning") is None
    with pytest.raises(MethodAttemptError):
        next_method("experience_system")


def test_attempt_events_are_append_only_and_enforce_transitions(method_attempt_store):
    topic_id = method_attempt_store
    queued = append_attempt_event(
        topic_id, "ai_learning", 1, "queued", event_key="ai:1:queued",
        evidence={"feature_enabled": True},
    )
    running = append_attempt_event(
        topic_id, "ai_learning", 1, "running", event_key="ai:1:running",
        external_run_ref="ai-run-17",
    )
    failed = append_attempt_event(
        topic_id, "ai_learning", 1, "unavailable", event_key="ai:1:unavailable",
        diagnosis={"reason": "browser_auth_required"},
    )

    assert [queued["event_sequence"], running["event_sequence"], failed["event_sequence"]] == [1, 2, 3]
    assert latest_attempt_event(topic_id, "ai_learning", 1)["status"] == "unavailable"
    assert [item["status"] for item in list_attempt_events(topic_id)] == [
        "queued", "running", "unavailable",
    ]
    with pytest.raises(MethodAttemptError, match="Invalid method-attempt transition"):
        append_attempt_event(
            topic_id, "ai_learning", 1, "running", event_key="ai:1:restarted",
        )
    with db.learning_connection() as connection:
        with pytest.raises(db.sqlite3.IntegrityError):
            connection.execute(
                "UPDATE learning_topic_method_attempt_events SET status='passed' WHERE id=?",
                (queued["id"],),
            )
        with pytest.raises(db.sqlite3.IntegrityError):
            connection.execute(
                "DELETE FROM learning_topic_method_attempt_events WHERE id=?",
                (queued["id"],),
            )


def test_cancelled_method_attempt_is_terminal_and_never_retried(method_attempt_store):
    topic_id = method_attempt_store
    append_attempt_event(
        topic_id, "ai_learning", 1, "queued", event_key="ai:cancel:queued"
    )
    append_attempt_event(
        topic_id, "ai_learning", 1, "running", event_key="ai:cancel:running"
    )
    cancelled = append_attempt_event(
        topic_id, "ai_learning", 1, "cancelled",
        event_key="ai:cancel:cancelled",
        diagnosis={"reason": "user_requested_stop"},
        evidence={"cancelled_by_user": True},
    )
    assert cancelled["status"] == "cancelled"
    assert latest_attempt_event(topic_id, "ai_learning", 1)["status"] == "cancelled"
    with pytest.raises(MethodAttemptError, match="Invalid method-attempt transition"):
        append_attempt_event(
            topic_id, "ai_learning", 1, "failed", event_key="ai:cancel:late-failure"
        )


def test_attempt_event_idempotency_is_safe_under_concurrent_delivery(method_attempt_store):
    topic_id = method_attempt_store

    def append():
        return append_attempt_event(
            topic_id, "basic_learning", 1, "queued",
            event_key="basic:1:queued",
            evidence={"source": "test"},
        )

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _index: append(), range(8)))

    assert len({item["id"] for item in results}) == 1
    assert sum(not item["idempotent_replay"] for item in results) == 1
    assert sum(item["idempotent_replay"] for item in results) == 7
    assert len(list_attempt_events(topic_id)) == 1


def test_event_key_cannot_be_reused_for_different_evidence(method_attempt_store):
    topic_id = method_attempt_store
    append_attempt_event(
        topic_id, "topic_learning", 1, "queued",
        event_key="topic:1:queued", evidence={"source_hash": "a"},
    )
    with pytest.raises(MethodAttemptError, match="reused with different event data"):
        append_attempt_event(
            topic_id, "topic_learning", 1, "queued",
            event_key="topic:1:queued", evidence={"source_hash": "b"},
        )


@pytest.mark.parametrize(
    "kwargs",
    [
        {"path_topic_id": True},
        {"attempt_number": 0},
        {"method": "experience_system"},
        {"status": "success"},
        {"content_sha256": "not-a-hash"},
        {"score": float("nan")},
    ],
)
def test_attempt_event_contract_rejects_invalid_values(method_attempt_store, kwargs):
    values = {
        "path_topic_id": method_attempt_store,
        "method": "ai_learning",
        "attempt_number": 1,
        "status": "queued",
        "event_key": "invalid-contract",
    }
    values.update(kwargs)
    with pytest.raises(MethodAttemptError):
        append_attempt_event(**values)
