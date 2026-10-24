import hashlib
import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from app import db, learning_candidate_channels as channels, learning


@pytest.fixture
def candidate_store(tmp_path, monkeypatch):
    monkeypatch.setattr(db.defaults, "settings_db_path", str(tmp_path / "settings.db"))
    monkeypatch.setattr(db.defaults, "learning_db_path", str(tmp_path / "learning.db"))
    db.init_databases()
    job = learning.create_job("Channel ledger candidate", source_text="Reference text for the candidate.")
    content = "A staged lesson whose channel results must remain auditable."
    with db.learning_connection() as connection:
        cursor = connection.execute(
            """INSERT INTO temporary_knowledge_candidates(
                   job_id, topic, content, source_kind, source_urls_json,
                   content_sha256, quality_report_json
               ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                job["id"], "Channel ledger candidate", content, "user_provided_text",
                json.dumps(["https://docs.python.org/3/"]),
                hashlib.sha256(content.encode("utf-8")).hexdigest(),
                json.dumps({"accepted": True, "score": 90}),
            ),
        )
        candidate_id = int(cursor.lastrowid)
    return candidate_id


def test_channel_run_reservation_is_idempotent_and_does_not_promote(candidate_store):
    first = channels.reserve_channel_run(candidate_store, "research_web", "research-v1")
    second = channels.reserve_channel_run(candidate_store, "research_web", "research-v1")

    assert first["should_execute"] is True
    assert second["should_execute"] is False
    assert first["id"] == second["id"]
    assert first["status"] == "running"

    with db.learning_connection() as connection:
        candidate = connection.execute(
            "SELECT status, promoted_knowledge_id FROM temporary_knowledge_candidates WHERE id=?",
            (candidate_store,),
        ).fetchone()
        runs = connection.execute(
            "SELECT COUNT(*) FROM learning_candidate_channel_runs WHERE candidate_id=?",
            (candidate_store,),
        ).fetchone()[0]
    assert candidate["status"] == "awaiting_test"
    assert candidate["promoted_knowledge_id"] is None
    assert runs == 1


def test_reclaimed_channel_run_fences_out_stale_worker(candidate_store):
    first = channels.reserve_channel_run(candidate_store, "sandbox", "recoverable-run")
    second = channels.reserve_channel_run(
        candidate_store, "sandbox", "recoverable-run", reclaim_running=True
    )
    assert second["id"] == first["id"]
    assert second["attempt_generation"] == first["attempt_generation"] + 1
    assert second["should_execute"] is True
    with pytest.raises(channels.CandidateChannelError, match="stale channel worker"):
        channels.finish_channel_run(
            first["id"], "succeeded", result={"status": "completed"},
            attempt_generation=first["attempt_generation"],
        )
    completed = channels.finish_channel_run(
        second["id"], "succeeded", result={"status": "completed"},
        attempt_generation=second["attempt_generation"],
    )
    assert completed["status"] == "succeeded"


def test_channel_run_terminal_result_is_immutable_and_completion_is_idempotent(candidate_store):
    run = channels.reserve_channel_run(candidate_store, "peer_ai", "peer-v1")
    result = {"provider_count": 2, "responses_hashed": True}
    finished = channels.finish_channel_run(run["id"], "succeeded", result=result)
    repeated = channels.finish_channel_run(run["id"], "succeeded", result=result)

    assert finished["status"] == "succeeded"
    assert finished["result"] == result
    assert finished["result_sha256"] == repeated["result_sha256"]
    assert repeated["finished_at"] == finished["finished_at"]
    assert repeated["should_execute"] is False

    with pytest.raises(channels.CandidateChannelError, match="terminal channel result is immutable"):
        channels.finish_channel_run(run["id"], "failed", result={"reason": "different"})


def test_channel_run_blocks_modified_candidate_content(candidate_store):
    with db.learning_connection() as connection:
        connection.execute(
            "UPDATE temporary_knowledge_candidates SET content='tampered' WHERE id=?",
            (candidate_store,),
        )
    with pytest.raises(channels.CandidateChannelError, match="content hash mismatch"):
        channels.reserve_channel_run(candidate_store, "sandbox", "sandbox-v1")


def test_concurrent_channel_reservation_allows_only_one_executor(candidate_store):
    def reserve(_):
        return channels.reserve_channel_run(candidate_store, "research_web", "same-key")

    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = list(pool.map(reserve, range(2)))

    assert first["id"] == second["id"]
    assert sorted([first["should_execute"], second["should_execute"]]) == [False, True]
    history = channels.list_candidate_channel_runs(candidate_store)
    assert len(history) == 1
    assert history[0]["channel"] == "research_web"


@pytest.mark.parametrize("channel", ["research_web", "peer_ai", "sandbox"])
def test_channel_run_result_size_and_status_are_validated(candidate_store, channel):
    run = channels.reserve_channel_run(candidate_store, channel, f"key-{channel}")
    with pytest.raises(channels.CandidateChannelError, match="may finish only"):
        channels.finish_channel_run(run["id"], "running")
    with pytest.raises(channels.CandidateChannelError, match="16 KiB"):
        channels.finish_channel_run(run["id"], "succeeded", result={"payload": "x" * 17000})
    with pytest.raises(channels.CandidateChannelError, match="invalid Unicode"):
        channels.finish_channel_run(run["id"], "succeeded", result={"payload": "\ud800"})


def test_channel_reservation_rejects_invalid_keys_channels_and_missing_candidates(candidate_store):
    with pytest.raises(channels.CandidateChannelError, match="candidate_id"):
        channels.reserve_channel_run(True, "research_web", "valid-key")
    with pytest.raises(channels.CandidateChannelError, match="candidate_id"):
        channels.reserve_channel_run(0, "research_web", "valid-key")
    with pytest.raises(channels.CandidateChannelError, match="Idempotency key"):
        channels.reserve_channel_run(candidate_store, "research_web", "   ")
    with pytest.raises(channels.CandidateChannelError, match="Unsupported"):
        channels.reserve_channel_run(candidate_store, "unknown", "valid-key")
    with pytest.raises(channels.CandidateChannelError, match="does not exist"):
        channels.reserve_channel_run(999999, "research_web", "valid-key")


def test_duplicate_reservation_remains_idempotent_after_candidate_status_changes(candidate_store):
    first = channels.reserve_channel_run(candidate_store, "research_web", "stable-key")
    with db.learning_connection() as connection:
        connection.execute(
            "UPDATE temporary_knowledge_candidates SET status='passed' WHERE id=?",
            (candidate_store,),
        )

    repeated = channels.reserve_channel_run(candidate_store, "research_web", "stable-key")
    assert repeated["id"] == first["id"]
    assert repeated["should_execute"] is False
    assert repeated["status"] == "running"

    with pytest.raises(channels.CandidateChannelError, match="awaiting independent tests"):
        channels.reserve_channel_run(candidate_store, "research_web", "new-key")
