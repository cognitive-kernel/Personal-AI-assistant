"""Governed self-improvement proposal ledger.

This module records and validates patches but never applies them. Approval means
"approved to open/review as a PR", not deployed or trusted production code.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import db
from .schema_registry import initialize_database_file
from .db import learning_connection

_MAX_PATCH_BYTES = 256 * 1024
_MAX_FILES = 20
_ALLOWED_PATH = re.compile(r"^(?:app/.+\.py|tests/.+\.py|docs/.+\.md)$")
_DIFF_HEADER = re.compile(r"^diff --git a/([^\s]+) b/([^\s]+)$")
_ALLOWED_STATUSES = {"proposed", "validated", "approved_for_pr", "rejected"}


class ProposalError(ValueError):
    """A proposal violates the governance or validation contract."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)


def _hash_patch(patch: str) -> str:
    return hashlib.sha256(patch.encode("utf-8")).hexdigest()


def _repo_root() -> Path:
    raw = os.getenv("SELF_IMPROVEMENT_REPO_ROOT", "").strip()
    if not raw:
        raise ProposalError("Self-improvement is disabled: configure SELF_IMPROVEMENT_REPO_ROOT explicitly.")
    candidate = Path(raw).expanduser()
    if not candidate.is_absolute():
        raise ProposalError("SELF_IMPROVEMENT_REPO_ROOT must be an absolute path.")
    try:
        root = candidate.resolve(strict=True)
    except OSError as exc:
        raise ProposalError("Configured repository root does not exist.") from exc
    if not root.is_dir() or not (root / ".git").exists() or not (root / "app").is_dir() or not (root / "tests").is_dir():
        raise ProposalError("Configured path must be the project Git checkout containing app/ and tests/.")
    return root


def _git(root: Path, args: list[str], *, input_text: str | None = None) -> subprocess.CompletedProcess[str]:
    environment = {
        "PATH": os.environ.get("PATH", ""),
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_OPTIONAL_LOCKS": "0",
    }
    try:
        return subprocess.run(
            ["git", "-C", str(root), *args],
            input=input_text,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=8,
            check=False,
            shell=False,
            env=environment,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ProposalError("Git validation could not complete safely.") from exc


def validate_patch_format(patch: str) -> dict[str, Any]:
    if not isinstance(patch, str) or not patch.strip():
        raise ProposalError("patch must be non-empty unified diff text.")
    try:
        encoded = patch.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ProposalError("patch must be valid UTF-8 text.") from exc
    if len(encoded) > _MAX_PATCH_BYTES or "\x00" in patch:
        raise ProposalError("patch exceeds the 256 KiB limit or contains NUL characters.")
    if "GIT binary patch" in patch or "Binary files " in patch:
        raise ProposalError("Binary patches are not allowed.")
    forbidden = ("rename from ", "rename to ", "deleted file mode ", "new file mode 120000", "new file mode 160000")
    if any(token in patch for token in forbidden):
        raise ProposalError("Renames, deletions, symlinks, and submodules are not allowed.")
    if re.search(r"^index [0-9a-f]+\.\.0{6,}(?:\s|$)", patch, re.MULTILINE):
        raise ProposalError("Deletion patches are not allowed.")

    paths: list[str] = []
    current_path: str | None = None
    has_hunk = False
    for line in patch.splitlines():
        if line.startswith("diff --git "):
            match = _DIFF_HEADER.fullmatch(line)
            if not match:
                raise ProposalError("Patch contains an unsupported or ambiguous diff path.")
            old_path, new_path = match.groups()
            if old_path != new_path:
                raise ProposalError("Each patch file must keep the same path; renames are not allowed.")
            path = old_path
            if path.startswith("/") or "\\" in path or any(part in {"", ".", ".."} for part in path.split("/")):
                raise ProposalError("Patch paths must be relative and cannot traverse directories.")
            if not _ALLOWED_PATH.fullmatch(path):
                raise ProposalError("Only Python files under app/ or tests/ and Markdown under docs/ are allowed.")
            if path.startswith("docs/") and path.endswith(".md") is False:
                raise ProposalError("Only Markdown documentation files are allowed.")
            paths.append(path)
            current_path = path
        elif line.startswith("--- "):
            old = line[4:].split("\t", 1)[0]
            if old != "/dev/null" and (not current_path or old != "a/" + current_path):
                raise ProposalError("Patch old-file header does not match the reviewed path.")
            if old == "/dev/null" and not current_path:
                raise ProposalError("New-file patch is missing its diff header.")
        elif line.startswith("+++ "):
            new = line[4:].split("\t", 1)[0]
            if new != "b/" + current_path if current_path else True:
                raise ProposalError("Patch new-file header does not match the reviewed path.")
        elif line.startswith("@@ "):
            if current_path is None:
                raise ProposalError("Patch hunk appears before a file header.")
            has_hunk = True
    if not paths or len(paths) > _MAX_FILES or len(set(paths)) != len(paths):
        raise ProposalError("Patch must touch between 1 and 20 unique allow-listed files.")
    if not has_hunk:
        raise ProposalError("Patch must contain at least one unified diff hunk.")
    return {"files": paths, "file_count": len(paths), "sha256": _hash_patch(patch), "bytes": len(encoded)}


def _append_event(conn, proposal_id: int, event_type: str, details: dict[str, Any]) -> None:
    conn.execute(
        "INSERT INTO self_improvement_events(proposal_id, event_type, actor, details_json, created_at) VALUES (?, ?, ?, ?, ?)",
        (proposal_id, event_type, "local_user", _json(details), _now()),
    )


def _proposal(row: Any, *, include_patch: bool = False) -> dict[str, Any]:
    result = {
        "id": int(row["id"]),
        "title": row["title"],
        "rationale": row["rationale"],
        "patch_sha256": row["patch_sha256"],
        "status": row["status"],
        "base_commit_sha": row["base_commit_sha"],
        "validation_report": json.loads(row["validation_report_json"]) if row["validation_report_json"] else None,
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "validated_at": row["validated_at"],
        "approved_at": row["approved_at"],
        "rejected_at": row["rejected_at"],
    }
    if include_patch:
        result["patch"] = row["patch_text"]
    return result


def create_proposal(title: str, rationale: str, patch: str) -> dict[str, Any]:
    title = title.strip() if isinstance(title, str) else ""
    rationale = rationale.strip() if isinstance(rationale, str) else ""
    if not title or len(title) > 160:
        raise ProposalError("title must contain 1–160 characters.")
    if not rationale or len(rationale) > 4000:
        raise ProposalError("rationale must contain 1–4000 characters.")
    patch_meta = validate_patch_format(patch)
    root = _repo_root()
    head = _git(root, ["rev-parse", "--verify", "HEAD"])
    if head.returncode != 0:
        raise ProposalError("Configured repository has no verifiable HEAD commit.")
    base_commit = head.stdout.strip()
    if not re.fullmatch(r"[0-9a-f]{40,64}", base_commit):
        raise ProposalError("Git returned an invalid commit identifier.")
    db.init_databases()
    now = _now()
    with learning_connection() as conn:
        cursor = conn.execute(
            """INSERT INTO self_improvement_proposals(
                   title, rationale, patch_text, patch_sha256, status, base_commit_sha,
                   validation_report_json, created_at, updated_at
               ) VALUES (?, ?, ?, ?, 'proposed', ?, NULL, ?, ?)""",
            (title, rationale, patch, patch_meta["sha256"], base_commit, now, now),
        )
        proposal_id = int(cursor.lastrowid)
        _append_event(conn, proposal_id, "proposed", {
            "patch_sha256": patch_meta["sha256"],
            "file_count": patch_meta["file_count"],
            "files": patch_meta["files"],
            "base_commit_sha": base_commit,
        })
        row = conn.execute("SELECT * FROM self_improvement_proposals WHERE id=?", (proposal_id,)).fetchone()
    return _proposal(row, include_patch=True)


def get_proposal(proposal_id: int) -> dict[str, Any] | None:
    if isinstance(proposal_id, bool) or not isinstance(proposal_id, int) or proposal_id < 1:
        raise ProposalError("proposal_id must be a positive integer.")
    db.init_databases()
    with learning_connection() as conn:
        row = conn.execute("SELECT * FROM self_improvement_proposals WHERE id=?", (proposal_id,)).fetchone()
    return _proposal(row, include_patch=True) if row else None


def list_proposals(limit: int = 50) -> list[dict[str, Any]]:
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
        raise ProposalError("limit must be an integer from 1 to 100.")
    db.init_databases()
    with learning_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM self_improvement_proposals ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
    return [_proposal(row) for row in rows]


def validate_proposal(proposal_id: int) -> dict[str, Any]:
    proposal = get_proposal(proposal_id)
    if proposal is None:
        raise KeyError(proposal_id)
    if proposal["status"] != "proposed":
        raise ProposalError("Only proposed changes can be validated.")
    patch = proposal["patch"]
    meta = validate_patch_format(patch)
    if meta["sha256"] != proposal["patch_sha256"]:
        raise ProposalError("Stored patch hash does not match the proposal record.")
    root = _repo_root()
    head = _git(root, ["rev-parse", "--verify", "HEAD"])
    if head.returncode != 0 or head.stdout.strip() != proposal["base_commit_sha"]:
        raise ProposalError("Repository HEAD changed since proposal creation; create a new proposal against the current base.")
    checked = _git(root, ["apply", "--check", "--whitespace=error-all", "-"], input_text=patch)
    report = {
        "patch_format": "passed",
        "patch_sha256": meta["sha256"],
        "files": meta["files"],
        "file_count": meta["file_count"],
        "base_commit_sha": proposal["base_commit_sha"],
        "apply_check": "passed" if checked.returncode == 0 else "failed",
        "diagnostic": (checked.stderr or checked.stdout).strip()[:1000],
        "tests_executed": False,
        "isolated_execution": False,
        "note": "Applicability check only. No tests were run, no files were modified, and no code was promoted.",
        "validated_at": _now(),
    }
    if checked.returncode != 0:
        raise ProposalError("Patch does not apply cleanly to the recorded base; no files were changed.")
    db.init_databases()
    with learning_connection() as conn:
        conn.execute(
            """UPDATE self_improvement_proposals
               SET status='validated', validation_report_json=?, validated_at=?, updated_at=?
               WHERE id=? AND status='proposed'""",
            (_json(report), report["validated_at"], report["validated_at"], proposal_id),
        )
        _append_event(conn, proposal_id, "validated", report)
        row = conn.execute("SELECT * FROM self_improvement_proposals WHERE id=?", (proposal_id,)).fetchone()
    return _proposal(row, include_patch=True)


def approve_for_pr(proposal_id: int, *, confirm: bool) -> dict[str, Any]:
    if confirm is not True:
        raise ProposalError("Explicit confirmation is required.")
    proposal = get_proposal(proposal_id)
    if proposal is None:
        raise KeyError(proposal_id)
    if proposal["status"] != "validated" or not proposal["validation_report"]:
        raise ProposalError("Only a validated proposal can be approved for a human-reviewed PR.")
    root = _repo_root()
    head = _git(root, ["rev-parse", "--verify", "HEAD"])
    if head.returncode != 0 or head.stdout.strip() != proposal["base_commit_sha"]:
        raise ProposalError("Repository HEAD changed after validation; validate a new proposal.")
    meta = validate_patch_format(proposal["patch"])
    if meta["sha256"] != proposal["patch_sha256"]:
        raise ProposalError("Stored patch hash changed; approval is blocked.")
    now = _now()
    db.init_databases()
    with learning_connection() as conn:
        conn.execute(
            """UPDATE self_improvement_proposals
               SET status='approved_for_pr', approved_at=?, updated_at=?
               WHERE id=? AND status='validated'""",
            (now, now, proposal_id),
        )
        _append_event(conn, proposal_id, "approved_for_pr", {
            "patch_sha256": meta["sha256"],
            "base_commit_sha": proposal["base_commit_sha"],
            "confirm": True,
            "code_applied": False,
        })
        row = conn.execute("SELECT * FROM self_improvement_proposals WHERE id=?", (proposal_id,)).fetchone()
    return _proposal(row, include_patch=True)


def reject_proposal(proposal_id: int, *, confirm: bool, reason: str = "") -> dict[str, Any]:
    if confirm is not True:
        raise ProposalError("Explicit confirmation is required.")
    reason = reason.strip() if isinstance(reason, str) else ""
    if len(reason) > 1000:
        raise ProposalError("reason must be at most 1000 characters.")
    proposal = get_proposal(proposal_id)
    if proposal is None:
        raise KeyError(proposal_id)
    if proposal["status"] not in {"proposed", "validated"}:
        raise ProposalError("Only proposed or validated changes can be rejected.")
    now = _now()
    db.init_databases()
    with learning_connection() as conn:
        conn.execute(
            """UPDATE self_improvement_proposals
               SET status='rejected', rejected_at=?, updated_at=?
               WHERE id=? AND status IN ('proposed','validated')""",
            (now, now, proposal_id),
        )
        _append_event(conn, proposal_id, "rejected", {"reason": reason, "confirm": True})
        row = conn.execute("SELECT * FROM self_improvement_proposals WHERE id=?", (proposal_id,)).fetchone()
    return _proposal(row, include_patch=True)


def proposal_events(proposal_id: int) -> list[dict[str, Any]]:
    if isinstance(proposal_id, bool) or not isinstance(proposal_id, int) or proposal_id < 1:
        raise ProposalError("proposal_id must be a positive integer.")
    db.init_databases()
    with learning_connection() as conn:
        if conn.execute("SELECT 1 FROM self_improvement_proposals WHERE id=?", (proposal_id,)).fetchone() is None:
            raise KeyError(proposal_id)
        rows = conn.execute(
            "SELECT event_type, actor, details_json, created_at FROM self_improvement_events WHERE proposal_id=? ORDER BY id",
            (proposal_id,),
        ).fetchall()
    return [
        {"event_type": row["event_type"], "actor": row["actor"], "details": json.loads(row["details_json"]), "created_at": row["created_at"]}
        for row in rows
    ]
