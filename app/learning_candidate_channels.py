"""Idempotent execution ledger for independent learning-candidate review channels.

This module records channel attempts only. It never marks a candidate passed,
changes trust level, or promotes knowledge based on a channel result.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from typing import Any, Literal

from .db import learning_connection

Channel = Literal["research_web", "peer_ai", "sandbox"]
TerminalStatus = Literal["succeeded", "failed", "skipped"]
_ALLOWED_CHANNELS = {"research_web", "peer_ai", "sandbox"}
_ALLOWED_TERMINAL = {"succeeded", "failed", "skipped"}
_ERROR_CODE = re.compile(r"^[a-zA-Z0-9_.-]{1,80}$")
_MAX_RESULT_BYTES = 16 * 1024


class CandidateChannelError(ValueError):
    """A channel run cannot safely be created or finalized."""


def _encoded_result(result: dict[str, Any] | None) -> tuple[str, str]:
    if result is None:
        result = {}
    if not isinstance(result, dict):
        raise CandidateChannelError("Channel result must be a JSON object.")
    try:
        encoded = json.dumps(
            result, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        )
    except (TypeError, ValueError) as exc:
        raise CandidateChannelError("Channel result must contain finite JSON values only.") from exc
    if len(encoded.encode("utf-8")) > _MAX_RESULT_BYTES:
        raise CandidateChannelError("Channel result exceeds the 16 KiB metadata limit.")
    return encoded, hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _run(row: sqlite3.Row, *, should_execute: bool = False) -> dict[str, Any]:
    result = dict(row)
    try:
        result["result"] = json.loads(result.pop("result_json"))
    except (TypeError, json.JSONDecodeError) as exc:
        raise CandidateChannelError("Stored channel result is invalid.") from exc
    result["should_execute"] = should_execute
    return result


def reserve_channel_run(
    candidate_id: int,
    channel: Channel,
    idempotency_key: str,
) -> dict[str, Any]:
    """Atomically reserve one channel run; only the first caller may execute it."""
    if channel not in _ALLOWED_CHANNELS:
        raise CandidateChannelError("Unsupported candidate review channel.")
    if not isinstance(idempotency_key, str) or not 1 <= len(idempotency_key.strip()) <= 128:
        raise CandidateChannelError("Idempotency key must contain 1–128 non-whitespace characters.")
    key = idempotency_key.strip()

    try:
        with learning_connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            candidate = connection.execute(
                """SELECT id, content, content_sha256, status
                   FROM temporary_knowledge_candidates WHERE id=?""",
                (candidate_id,),
            ).fetchone()
            if candidate is None:
                raise CandidateChannelError("Learning candidate does not exist.")
            actual_hash = hashlib.sha256(candidate["content"].encode("utf-8")).hexdigest()
            if actual_hash != candidate["content_sha256"]:
                raise CandidateChannelError("Candidate content hash mismatch; channel execution is blocked.")
            if candidate["status"] != "awaiting_test":
                raise CandidateChannelError("Only candidates awaiting independent tests can start channel runs.")

            existing = connection.execute(
                """SELECT * FROM learning_candidate_channel_runs
                   WHERE candidate_id=? AND channel=? AND idempotency_key=?""",
                (candidate_id, channel, key),
            ).fetchone()
            if existing is not None:
                if existing["candidate_content_sha256"] != actual_hash:
                    raise CandidateChannelError("Idempotency key is bound to a different candidate content hash.")
                return _run(existing, should_execute=False)

            cursor = connection.execute(
                """INSERT INTO learning_candidate_channel_runs(
                       candidate_id, channel, idempotency_key, candidate_content_sha256,
                       status, result_json
                   ) VALUES (?, ?, ?, ?, 'running', '{}')""",
                (candidate_id, channel, key, actual_hash),
            )
            row = connection.execute(
                "SELECT * FROM learning_candidate_channel_runs WHERE id=?",
                (int(cursor.lastrowid),),
            ).fetchone()
            if row is None:
                raise CandidateChannelError("Channel run reservation could not be read back.")
            return _run(row, should_execute=True)
    except CandidateChannelError:
        raise
    except sqlite3.Error as exc:
        raise CandidateChannelError("Candidate channel reservation failed safely.") from exc


def finish_channel_run(
    run_id: int,
    status: TerminalStatus,
    *,
    result: dict[str, Any] | None = None,
    error_code: str | None = None,
) -> dict[str, Any]:
    """Record immutable terminal metadata; repeated identical completion is idempotent."""
    if status not in _ALLOWED_TERMINAL:
        raise CandidateChannelError("Channel runs may finish only as succeeded, failed, or skipped.")
    if error_code is not None and (
        not isinstance(error_code, str) or _ERROR_CODE.fullmatch(error_code) is None
    ):
        raise CandidateChannelError("Invalid channel error code.")
    if status == "succeeded" and error_code is not None:
        raise CandidateChannelError("Successful channel runs cannot have an error code.")
    encoded, result_hash = _encoded_result(result)

    try:
        with learning_connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM learning_candidate_channel_runs WHERE id=?",
                (run_id,),
            ).fetchone()
            if row is None:
                raise CandidateChannelError("Learning candidate channel run does not exist.")
            if row["status"] != "running":
                same_completion = (
                    row["status"] == status
                    and row["result_sha256"] == result_hash
                    and row["error_code"] == error_code
                )
                if not same_completion:
                    raise CandidateChannelError("A terminal channel result is immutable.")
                return _run(row, should_execute=False)

            cursor = connection.execute(
                """UPDATE learning_candidate_channel_runs
                   SET status=?, result_json=?, result_sha256=?, error_code=?,
                       finished_at=CURRENT_TIMESTAMP, updated_at=CURRENT_TIMESTAMP
                   WHERE id=? AND status='running'""",
                (status, encoded, result_hash, error_code, run_id),
            )
            if cursor.rowcount != 1:
                raise CandidateChannelError("Channel run changed while recording its result.")
            saved = connection.execute(
                "SELECT * FROM learning_candidate_channel_runs WHERE id=?",
                (run_id,),
            ).fetchone()
            if saved is None:
                raise CandidateChannelError("Completed channel run could not be read back.")
            return _run(saved, should_execute=False)
    except CandidateChannelError:
        raise
    except sqlite3.Error as exc:
        raise CandidateChannelError("Candidate channel result could not be recorded.") from exc


def list_candidate_channel_runs(candidate_id: int) -> list[dict[str, Any]]:
    """Return bounded audit metadata in creation order; never return candidate text."""
    try:
        with learning_connection() as connection:
            candidate = connection.execute(
                "SELECT id FROM temporary_knowledge_candidates WHERE id=?",
                (candidate_id,),
            ).fetchone()
            if candidate is None:
                raise CandidateChannelError("Learning candidate does not exist.")
            rows = connection.execute(
                """SELECT * FROM learning_candidate_channel_runs
                   WHERE candidate_id=? ORDER BY id ASC LIMIT 100""",
                (candidate_id,),
            ).fetchall()
        return [_run(row) for row in rows]
    except CandidateChannelError:
        raise
    except sqlite3.Error as exc:
        raise CandidateChannelError("Candidate channel history is unavailable.") from exc
