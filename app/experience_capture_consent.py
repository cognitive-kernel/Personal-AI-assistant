"""Persistent, auditable opt-in consent for local research-chat capture."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from . import db

POLICY_VERSION = "experience-auto-capture-v1"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def get_consent() -> dict[str, Any]:
    with db.settings_connection() as connection:
        row = connection.execute(
            """SELECT enabled, policy_version, consented_at, revoked_at, updated_at
               FROM experience_auto_capture_consent WHERE id=1"""
        ).fetchone()
        events = connection.execute(
            """SELECT id, event_type, policy_version, created_at
               FROM experience_auto_capture_consent_events
               ORDER BY id DESC LIMIT 50"""
        ).fetchall()
    if row is None:
        return {
            "enabled": False,
            "policy_version": POLICY_VERSION,
            "consented_at": None,
            "revoked_at": None,
            "updated_at": None,
            "events": [dict(event) for event in events],
        }
    return {
        "enabled": bool(row["enabled"]),
        "policy_version": row["policy_version"],
        "consented_at": row["consented_at"],
        "revoked_at": row["revoked_at"],
        "updated_at": row["updated_at"],
        "events": [dict(event) for event in events],
    }


def is_enabled() -> bool:
    """Fail closed when the consent database or record is unavailable."""
    try:
        return get_consent()["enabled"] is True
    except Exception:
        return False


def grant_consent() -> dict[str, Any]:
    now = _now()
    with db.settings_connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            """UPDATE experience_auto_capture_consent
               SET enabled=1, policy_version=?, consented_at=?, revoked_at=NULL, updated_at=?
               WHERE id=1""",
            (POLICY_VERSION, now, now),
        )
        connection.execute(
            """INSERT INTO experience_auto_capture_consent_events(event_type, policy_version)
               VALUES ('consent_granted', ?)""",
            (POLICY_VERSION,),
        )
    return get_consent()


def revoke_consent() -> dict[str, Any]:
    now = _now()
    with db.settings_connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            """UPDATE experience_auto_capture_consent
               SET enabled=0, revoked_at=?, updated_at=?
               WHERE id=1""",
            (now, now),
        )
        connection.execute(
            """INSERT INTO experience_auto_capture_consent_events(event_type, policy_version)
               VALUES ('consent_revoked', ?)""",
            (POLICY_VERSION,),
        )
    return get_consent()
