"""Governed self-improvement proposal ledger.

This module records and validates patches but never applies them. Approval means
"approved to open/review as a PR", not deployed or trusted production code.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import subprocess
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
import urllib.error
import urllib.request
from typing import Any

from . import db, sandbox, sandbox_consent, sandbox_tasks
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


def _validate_repo_paths(root: Path, paths: list[str]) -> None:
    resolved_root = root.resolve(strict=True)
    for relative in paths:
        candidate = resolved_root / relative
        parent = candidate.parent
        chain = []
        while parent != resolved_root and parent != parent.parent:
            chain.append(parent)
            parent = parent.parent
        for directory in reversed(chain):
            if directory.is_symlink():
                raise ProposalError("Patch paths cannot traverse symbolic-link directories.")
        if candidate.is_symlink():
            raise ProposalError("Patch targets cannot be symbolic links.")
        try:
            candidate.resolve(strict=False).relative_to(resolved_root)
        except ValueError as exc:
            raise ProposalError("Patch target resolves outside the configured repository.") from exc
        if candidate.exists() and not candidate.is_file():
            raise ProposalError("Patch targets must be regular files.")


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


def _github_api(method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """Call the GitHub REST API without placing credentials in command arguments."""
    token = os.getenv("SELF_IMPROVEMENT_GITHUB_TOKEN", "").strip()
    repository = os.getenv("SELF_IMPROVEMENT_GITHUB_REPOSITORY", "").strip()
    if not token:
        raise ProposalError("GitHub PR creation is disabled: SELF_IMPROVEMENT_GITHUB_TOKEN is not configured.")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
        raise ProposalError("GitHub PR creation is disabled: configure SELF_IMPROVEMENT_GITHUB_REPOSITORY as owner/repository.")
    if not path.startswith("/"):
        raise ProposalError("GitHub API path is invalid.")
    request_body = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        "https://api.github.com/repos/" + repository + path,
        data=request_body,
        method=method,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": "Bearer " + token,
            "X-GitHub-Api-Version": "2022-11-28",
            **({"Content-Type": "application/json"} if request_body is not None else {}),
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            raw = response.read(1024 * 1024)
            value = json.loads(raw.decode("utf-8"))
            if not isinstance(value, dict):
                raise ProposalError("GitHub returned an unexpected API response.")
            return value
    except urllib.error.HTTPError as exc:
        # Do not surface provider response bodies, which may contain sensitive metadata.
        if exc.code == 404:
            raise ProposalError("The configured GitHub repository, base ref, or commit was not found.") from None
        if exc.code == 401 or exc.code == 403:
            raise ProposalError("GitHub denied the request; verify token permissions and repository access.") from None
        if exc.code == 422:
            raise ProposalError("GitHub rejected the requested branch or pull request; check for an existing ref or PR.") from None
        raise ProposalError("GitHub API request failed with HTTP " + str(exc.code) + ".") from None
    except (OSError, TimeoutError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProposalError("GitHub API is unavailable or returned invalid JSON.") from exc


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
    forbidden = (
        "rename from ", "rename to ", "deleted file mode ", "old mode ",
        "new mode ", "new file mode 120000", "new file mode 160000",
    )
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
        "static_check": json.loads(row["static_check_json"]) if row["static_check_json"] else None,
        "static_checked_at": row["static_checked_at"],
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
    _validate_repo_paths(root, patch_meta["files"])
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



def _test_file_metrics(path: Path) -> dict[str, Any]:
    """Collect conservative, non-executing signals from a Python test file."""
    import ast

    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    tests: dict[str, dict[str, int]] = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test_"):
            tests[node.name] = {
                "asserts": sum(isinstance(child, ast.Assert) for child in ast.walk(node)),
                "raises": sum(
                    isinstance(child, ast.Call)
                    and isinstance(child.func, ast.Attribute)
                    and child.func.attr == "raises"
                    and isinstance(child.func.value, ast.Name)
                    and child.func.value.id == "pytest"
                    for child in ast.walk(node)
                ),
            }
    return {
        "tests": tests,
        "asserts": sum(item["asserts"] for item in tests.values()),
        "raises": sum(item["raises"] for item in tests.values()),
        "skip_markers": sum(source.count(marker) for marker in ("pytest.mark.skip", "pytest.mark.skipif", "pytest.mark.xfail")),
    }


def _test_integrity_report(base_root: Path, candidate_root: Path) -> dict[str, Any]:
    """Block obvious test weakening; this is a heuristic, not a behavioral proof."""
    baseline_files = sorted((base_root / "tests").rglob("test_*.py"))
    candidate_files = sorted((candidate_root / "tests").rglob("test_*.py"))
    baseline_by_rel = {str(path.relative_to(base_root)): path for path in baseline_files}
    candidate_by_rel = {str(path.relative_to(candidate_root)): path for path in candidate_files}
    if not baseline_by_rel:
        return {"status": "failed", "reason": "baseline_tests_missing", "tests_executed": False}
    removed_files = sorted(set(baseline_by_rel) - set(candidate_by_rel))
    if removed_files:
        return {
            "status": "failed", "reason": "test_files_removed",
            "removed_files": removed_files[:50], "tests_executed": False,
        }
    try:
        before = {name: _test_file_metrics(path) for name, path in baseline_by_rel.items()}
        after = {name: _test_file_metrics(path) for name, path in candidate_by_rel.items()}
    except (OSError, UnicodeError, SyntaxError, ValueError) as exc:
        return {
            "status": "failed", "reason": "test_source_unreadable",
            "diagnostic": type(exc).__name__, "tests_executed": False,
        }
    weakened = []
    for filename, metrics in before.items():
        candidate = after[filename]
        old_tests, new_tests = metrics["tests"], candidate["tests"]
        removed_tests = sorted(set(old_tests) - set(new_tests))
        if removed_tests:
            weakened.append({"file": filename, "reason": "test_functions_removed", "tests": removed_tests[:50]})
            continue
        for test_name, old in old_tests.items():
            new = new_tests[test_name]
            if new["asserts"] < old["asserts"] or new["raises"] < old["raises"]:
                weakened.append({
                    "file": filename, "test": test_name, "reason": "test_assertions_or_exception_checks_reduced",
                    "before": old, "after": new,
                })
        if candidate["skip_markers"] > metrics["skip_markers"]:
            weakened.append({
                "file": filename, "reason": "skip_or_xfail_markers_increased",
                "before": metrics["skip_markers"], "after": candidate["skip_markers"],
            })
    before_asserts = sum(item["asserts"] for item in before.values())
    after_asserts = sum(item["asserts"] for item in after.values())
    before_raises = sum(item["raises"] for item in before.values())
    after_raises = sum(item["raises"] for item in after.values())
    passed = not weakened and after_asserts >= before_asserts and after_raises >= before_raises
    return {
        "status": "passed" if passed else "failed",
        "files_compared": len(before),
        "baseline_asserts": before_asserts,
        "candidate_asserts": after_asserts,
        "baseline_pytest_raises": before_raises,
        "candidate_pytest_raises": after_raises,
        "weakened_signals": weakened[:100],
        "tests_executed": False,
        "note": "Static heuristic only; does not establish test quality or replace behavioral tests and human review.",
    }



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
    _validate_repo_paths(root, meta["files"])
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
        updated = conn.execute(
            """UPDATE self_improvement_proposals
               SET status='validated', validation_report_json=?, validated_at=?, updated_at=?
               WHERE id=? AND status='proposed'""",
            (_json(report), report["validated_at"], report["validated_at"], proposal_id),
        )
        if updated.rowcount != 1:
            raise ProposalError("Proposal state changed during validation; reload it before continuing.")
        _append_event(conn, proposal_id, "validated", report)
        row = conn.execute("SELECT * FROM self_improvement_proposals WHERE id=?", (proposal_id,)).fetchone()
    return _proposal(row, include_patch=True)


def static_check_proposal(proposal_id: int, *, confirm: bool) -> dict[str, Any]:
    """Apply a proposal only in a temporary worktree; check syntax and run tests in Docker."""
    if confirm is not True:
        raise ProposalError("Explicit confirmation is required for isolated syntax checking.")
    proposal = get_proposal(proposal_id)
    if proposal is None:
        raise KeyError(proposal_id)
    if proposal["status"] != "validated" or not proposal["validation_report"]:
        raise ProposalError("Only a patch that passed the Git applicability check can be syntax-checked.")
    if proposal["validation_report"].get("apply_check") != "passed":
        raise ProposalError("The patch applicability check has not passed.")
    if not sandbox_consent.is_enabled():
        raise ProposalError("Sandbox consent is disabled; grant local sandbox consent first.")
    try:
        configured_handler = sandbox_tasks.handler_from_environment()
    except ValueError as exc:
        raise ProposalError("Sandbox configuration is invalid.") from exc
    if configured_handler is None:
        raise ProposalError("Sandbox is disabled or not configured.")
    test_image = os.getenv("SANDBOX_TEST_IMAGE", "").strip()
    if not sandbox.is_valid_test_image_reference(test_image):
        raise ProposalError(
            "Configure SANDBOX_TEST_IMAGE as an immutable sha256 image ID/reference with pytest and project dependencies installed."
        )
    docker_executable = os.getenv("SANDBOX_DOCKER_EXECUTABLE", "").strip()
    image = os.getenv("SANDBOX_PYTHON_IMAGE", "").strip()

    meta = validate_patch_format(proposal["patch"])
    if meta["sha256"] != proposal["patch_sha256"]:
        raise ProposalError("Stored patch hash changed; syntax checking is blocked.")
    root = _repo_root()
    _validate_repo_paths(root, meta["files"])
    head = _git(root, ["rev-parse", "--verify", "HEAD"])
    if head.returncode != 0 or head.stdout.strip() != proposal["base_commit_sha"]:
        raise ProposalError("Repository HEAD changed after proposal creation; create a new proposal.")

    report: dict[str, Any] = {
        "status": "failed",
        "patch_sha256": meta["sha256"],
        "base_commit_sha": proposal["base_commit_sha"],
        "files": meta["files"],
        "syntax_check": "not_run",
        "files_checked": 0,
        "tests_executed": False,
        "candidate_code_executed": False,
        "sandbox": "docker",
        "created_at": _now(),
    }
    with tempfile.TemporaryDirectory(prefix="personal-ai-self-improvement-") as temp_root:
        workspace = Path(temp_root) / "checkout"
        added = _git(root, ["worktree", "add", "--detach", str(workspace), proposal["base_commit_sha"]])
        if added.returncode != 0:
            raise ProposalError("Could not create a temporary candidate worktree.")
        try:
            applied = _git(workspace, ["apply", "--whitespace=error-all", "-"], input_text=proposal["patch"])
            if applied.returncode != 0:
                report["diagnostic"] = (applied.stderr or applied.stdout).strip()[:1000]
                report["syntax_check"] = "patch_apply_failed"
            else:
                result = sandbox.run_workspace_syntax_check(
                    workspace,
                    docker_executable=docker_executable,
                    image=image,
                    timeout_seconds=60,
                    max_output_bytes=16384,
                )
                report["sandbox_status"] = result.status
                report["duration_ms"] = result.duration_ms
                report["diagnostic"] = (result.reason or result.stderr or "").strip()[:1000]
                report["test_integrity"] = _test_integrity_report(root, workspace)
                if report["test_integrity"]["status"] != "passed":
                    report["syntax_check"] = "test_integrity_failed"
                    report["diagnostic"] = "Static test-integrity guard detected possible test weakening."
                elif result.status == "succeeded":
                    try:
                        parsed = json.loads(result.stdout.strip().splitlines()[-1])
                    except (ValueError, IndexError):
                        parsed = {}
                    if parsed.get("status") == "passed" and isinstance(parsed.get("files_checked"), int) and parsed["files_checked"] > 0:
                        report["syntax_check"] = "passed"
                        report["files_checked"] = parsed["files_checked"]
                        behavioral = sandbox.run_workspace_tests(
                            workspace,
                            docker_executable=docker_executable,
                            image=test_image,
                            timeout_seconds=120,
                            max_output_bytes=65536,
                        )
                        report["behavioral_tests"] = {
                            "status": behavioral.status,
                            "exit_code": behavioral.exit_code,
                            "duration_ms": behavioral.duration_ms,
                            "stdout": behavioral.stdout[-12000:],
                            "stderr": behavioral.stderr[-12000:],
                            "reason": behavioral.reason,
                            "image": test_image,
                        }
                        report["tests_executed"] = behavioral.status in {
                            "succeeded", "failed", "timed_out", "output_limit_exceeded"
                        }
                        report["candidate_code_executed"] = report["tests_executed"]
                        if behavioral.status == "succeeded":
                            report["status"] = "passed"
                        else:
                            report["diagnostic"] = (
                                "Isolated behavioral tests did not pass: "
                                + (behavioral.reason or behavioral.status)
                            )[:1000]
                    else:
                        report["syntax_check"] = "invalid_checker_output"
                else:
                    report["syntax_check"] = result.status

        finally:
            _git(root, ["worktree", "remove", "--force", str(workspace)])
            _git(root, ["worktree", "prune"])
            shutil.rmtree(workspace, ignore_errors=True)

    report["completed_at"] = _now()
    db.init_databases()
    with learning_connection() as conn:
        updated = conn.execute(
            """UPDATE self_improvement_proposals
               SET static_check_json=?, static_checked_at=?, updated_at=?
               WHERE id=? AND status='validated' AND base_commit_sha=? AND patch_sha256=?""",
            (_json(report), report["completed_at"], report["completed_at"], proposal_id, proposal["base_commit_sha"], meta["sha256"]),
        )
        if updated.rowcount != 1:
            raise ProposalError("Proposal state changed during syntax checking; reload it before continuing.")
        _append_event(conn, proposal_id, "static_checked", report)
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
    if not proposal["static_check"] or proposal["static_check"].get("status") != "passed":
        raise ProposalError("An isolated syntax check, test-integrity guard, and behavior test run must pass before approval for a PR.")
    if not proposal["static_check"].get("test_integrity") or proposal["static_check"]["test_integrity"].get("status") != "passed":
        raise ProposalError("The static test-integrity guard must pass before approval for a PR.")
    root = _repo_root()
    head = _git(root, ["rev-parse", "--verify", "HEAD"])
    if head.returncode != 0 or head.stdout.strip() != proposal["base_commit_sha"]:
        raise ProposalError("Repository HEAD changed after validation; validate a new proposal.")
    meta = validate_patch_format(proposal["patch"])
    if meta["sha256"] != proposal["patch_sha256"]:
        raise ProposalError("Stored patch hash changed; approval is blocked.")
    if (
        proposal["static_check"].get("patch_sha256") != meta["sha256"]
        or proposal["static_check"].get("base_commit_sha") != proposal["base_commit_sha"]
    ):
        raise ProposalError("Isolated syntax-check evidence does not match this patch and base commit.")
    now = _now()
    db.init_databases()
    with learning_connection() as conn:
        updated = conn.execute(
            """UPDATE self_improvement_proposals
               SET status='approved_for_pr', approved_at=?, updated_at=?
               WHERE id=? AND status='validated'""",
            (now, now, proposal_id),
        )
        if updated.rowcount != 1:
            raise ProposalError("Proposal state changed during approval; reload it before continuing.")
        _append_event(conn, proposal_id, "approved_for_pr", {
            "patch_sha256": meta["sha256"],
            "base_commit_sha": proposal["base_commit_sha"],
            "confirm": True,
            "code_applied": False,
        })
        row = conn.execute("SELECT * FROM self_improvement_proposals WHERE id=?", (proposal_id,)).fetchone()
    return _proposal(row, include_patch=True)


def open_review_pull_request(proposal_id: int, *, confirm: bool) -> dict[str, Any]:
    """Publish an approved patch as a review-only GitHub PR; never merge or deploy it."""
    if confirm is not True:
        raise ProposalError("Explicit confirmation is required to publish a pull request.")
    proposal = get_proposal(proposal_id)
    if proposal is None:
        raise KeyError(proposal_id)
    if proposal["status"] != "approved_for_pr":
        raise ProposalError("Only a proposal approved for PR review can be published.")
    for event in proposal_events(proposal_id):
        if event["event_type"] == "pr_opened":
            return {"proposal_id": proposal_id, **event["details"], "already_opened": True}

    repository = os.getenv("SELF_IMPROVEMENT_GITHUB_REPOSITORY", "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
        raise ProposalError("Configure SELF_IMPROVEMENT_GITHUB_REPOSITORY as owner/repository.")
    if not os.getenv("SELF_IMPROVEMENT_GITHUB_TOKEN", "").strip():
        raise ProposalError("Configure SELF_IMPROVEMENT_GITHUB_TOKEN with repository contents and pull-request permissions.")
    base_branch = os.getenv("SELF_IMPROVEMENT_GITHUB_BASE_BRANCH", "main").strip()
    if not re.fullmatch(r"[A-Za-z0-9._/-]{1,120}", base_branch) or base_branch.startswith("/") or ".." in base_branch:
        raise ProposalError("Configured GitHub base branch is invalid.")

    root = _repo_root()
    head = _git(root, ["rev-parse", "--verify", "HEAD"])
    if head.returncode != 0 or head.stdout.strip() != proposal["base_commit_sha"]:
        raise ProposalError("Local repository HEAD changed after validation; validate a new proposal.")
    meta = validate_patch_format(proposal["patch"])
    if meta["sha256"] != proposal["patch_sha256"]:
        raise ProposalError("Stored patch hash changed; PR creation is blocked.")
    if (
        not proposal["static_check"]
        or proposal["static_check"].get("status") != "passed"
        or proposal["static_check"].get("patch_sha256") != meta["sha256"]
        or proposal["static_check"].get("base_commit_sha") != proposal["base_commit_sha"]
        or not proposal["static_check"].get("test_integrity")
        or proposal["static_check"]["test_integrity"].get("status") != "passed"
        or not proposal["static_check"].get("behavior_test")
        or proposal["static_check"]["behavior_test"].get("status") != "passed"
    ):
        raise ProposalError("Passing syntax, test-integrity, and isolated behavioral evidence for this exact patch is required.")

    base_ref = _github_api("GET", "/git/ref/heads/" + base_branch)
    remote_base_sha = base_ref.get("object", {}).get("sha")
    if remote_base_sha != proposal["base_commit_sha"]:
        raise ProposalError("Remote base branch has moved since validation; rebase/revalidate before publishing.")
    base_commit = _github_api("GET", "/git/commits/" + proposal["base_commit_sha"])
    base_tree = base_commit.get("tree", {}).get("sha")
    if not isinstance(base_tree, str) or not base_tree:
        raise ProposalError("GitHub did not return a valid base tree.")

    worktree_path: Path | None = None
    ref_created = False
    branch_name = "ai-proposal/" + str(proposal_id) + "-" + meta["sha256"][:12]
    try:
        with tempfile.TemporaryDirectory(prefix="ai-pr-worktree-") as temp_dir:
            worktree_path = Path(temp_dir) / "candidate"
            added = _git(root, ["worktree", "add", "--detach", str(worktree_path), proposal["base_commit_sha"]])
            if added.returncode != 0:
                raise ProposalError("A clean temporary worktree could not be created.")
            check = _git(worktree_path, ["apply", "--check"], input_text=proposal["patch"])
            if check.returncode != 0:
                raise ProposalError("The approved patch no longer applies cleanly to its recorded base.")
            applied = _git(worktree_path, ["apply"], input_text=proposal["patch"])
            if applied.returncode != 0:
                raise ProposalError("The approved patch could not be applied in the temporary worktree.")
            changed = _git(worktree_path, ["diff", "--name-only", "--no-ext-diff"])
            if changed.returncode != 0:
                raise ProposalError("Changed paths could not be enumerated safely.")
            changed_paths = changed.stdout.splitlines()
            if sorted(changed_paths) != sorted(meta["files"]):
                raise ProposalError("Applied patch paths do not match the reviewed patch manifest.")
            entries = []
            for relative in changed_paths:
                if not _ALLOWED_PATH.fullmatch(relative):
                    raise ProposalError("A changed path is outside the allowlist.")
                content = (worktree_path / relative).read_bytes()
                try:
                    decoded = content.decode("utf-8")
                except UnicodeDecodeError as exc:
                    raise ProposalError("Changed files must remain UTF-8 text.") from exc
                blob = _github_api("POST", "/git/blobs", {
                    "content": base64.b64encode(decoded.encode("utf-8")).decode("ascii"),
                    "encoding": "base64",
                })
                blob_sha = blob.get("sha")
                if not isinstance(blob_sha, str) or not re.fullmatch(r"[0-9a-f]{40,64}", blob_sha):
                    raise ProposalError("GitHub did not create a valid file blob.")
                entries.append({"path": relative, "mode": "100644", "type": "blob", "sha": blob_sha})

            tree = _github_api("POST", "/git/trees", {"base_tree": base_tree, "tree": entries})
            tree_sha = tree.get("sha")
            if not isinstance(tree_sha, str) or not re.fullmatch(r"[0-9a-f]{40,64}", tree_sha):
                raise ProposalError("GitHub did not create a valid candidate tree.")
            commit = _github_api("POST", "/git/commits", {
                "message": "AI proposal #" + str(proposal_id) + ": " + proposal["title"][:120],
                "tree": tree_sha,
                "parents": [proposal["base_commit_sha"]],
            })
            commit_sha = commit.get("sha")
            if not isinstance(commit_sha, str) or not re.fullmatch(r"[0-9a-f]{40,64}", commit_sha):
                raise ProposalError("GitHub did not create a valid candidate commit.")
            created_ref = _github_api("POST", "/git/refs", {
                "ref": "refs/heads/" + branch_name,
                "sha": commit_sha,
            })
            if created_ref.get("object", {}).get("sha") != commit_sha:
                raise ProposalError("GitHub did not confirm the candidate branch.")
            ref_created = True
            body = (
                "## Governed AI self-improvement proposal\n\n"
                + proposal["rationale"]
                + "\n\n## Evidence\n"
                + "- Proposal ID: " + str(proposal_id) + "\n"
                + "- Base commit: `" + proposal["base_commit_sha"] + "`\n"
                + "- Patch SHA-256: `" + meta["sha256"] + "`\n"
                + "- Isolated syntax, test-integrity, and behavioral gates passed for this exact patch.\n"
                + "- This PR was opened for human review only. It was not merged, deployed, or applied to the local working tree.\n"
            )
            pull = _github_api("POST", "/pulls", {
                "title": "[AI proposal #" + str(proposal_id) + "] " + proposal["title"][:160],
                "head": branch_name,
                "base": base_branch,
                "body": body,
                "draft": True,
            })
            number = pull.get("number")
            url = pull.get("html_url")
            if isinstance(number, bool) or not isinstance(number, int) or number < 1:
                raise ProposalError("GitHub did not return a valid pull request number.")
            if not isinstance(url, str) or not url.startswith("https://github.com/"):
                raise ProposalError("GitHub did not return a valid pull request URL.")
            details = {
                "pull_request_number": number,
                "pull_request_url": url,
                "branch": branch_name,
                "commit_sha": commit_sha,
                "base_branch": base_branch,
                "base_commit_sha": proposal["base_commit_sha"],
                "patch_sha256": meta["sha256"],
                "draft": True,
                "merged": False,
                "deployed": False,
            }
            db.init_databases()
            with learning_connection() as conn:
                _append_event(conn, proposal_id, "pr_opened", details)
            return {"proposal_id": proposal_id, **details, "already_opened": False}
    except Exception:
        if ref_created:
            try:
                _github_api("DELETE", "/git/refs/heads/" + branch_name)
            except Exception:
                pass
        raise
    finally:
        if worktree_path is not None:
            _git(root, ["worktree", "remove", "--force", str(worktree_path)])


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
        updated = conn.execute(
            """UPDATE self_improvement_proposals
               SET status='rejected', rejected_at=?, updated_at=?
               WHERE id=? AND status IN ('proposed','validated')""",
            (now, now, proposal_id),
        )
        if updated.rowcount != 1:
            raise ProposalError("Proposal state changed during rejection; reload it before continuing.")
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
