"""Pull-request lifecycle state machine. Effects are injected by the controller."""

from __future__ import annotations

import re
from typing import Any, Callable
from urllib.parse import quote


def find_open_autonomous_pr(token: str, repo: str, *, api: Callable[..., Any]) -> dict[str, Any] | None:
    pulls = api(token, repo, "GET", "pulls?state=open&per_page=100")
    if not isinstance(pulls, list):
        raise RuntimeError("GitHub returned an invalid open pull request listing.")
    for item in pulls:
        if not isinstance(item, dict):
            continue
        head = item.get("head") or {}
        base = item.get("base") or {}
        author = (item.get("user") or {}).get("login")
        head_repository = (head.get("repo") or {}).get("full_name")
        branch = head.get("ref")
        if (
            author == "github-actions[bot]"
            and head_repository == repo
            and isinstance(branch, str)
            and branch.startswith("codex/local-engineering-")
            and base.get("ref") == "main"
        ):
            return {
                "number": item.get("number"),
                "url": item.get("html_url"),
                "head_sha": head.get("sha"),
                "base_sha": base.get("sha"),
                "branch": branch,
                "draft": bool(item.get("draft", False)),
                "node_id": item.get("node_id"),
            }
    return None


def complete_pending_pr(token: str, repo: str, pending: dict[str, Any], run_id: str, task: int, *, api: Callable[..., Any], graphql: Callable[..., Any], save_pending_pr: Callable[..., Any], dispatch_and_wait_all: Callable[..., Any]) -> dict[str, Any]:
    number = pending.get("number")
    if not isinstance(number, int) or isinstance(number, bool):
        raise RuntimeError("The pending autonomous PR has no valid number.")
    current = api(token, repo, "GET", f"pulls/{number}")
    head = current.get("head") or {}
    base = current.get("base") or {}
    branch = head.get("ref")
    head_sha = head.get("sha")
    base_sha = base.get("sha")
    author = (current.get("user") or {}).get("login")
    head_repository = (head.get("repo") or {}).get("full_name")
    if (
        author != "github-actions[bot]"
        or head_repository != repo
        or current.get("state") != "open"
        or current.get("merged") is True
        or base.get("ref") != "main"
        or not isinstance(branch, str)
        or not branch.startswith("codex/local-engineering-")
        or head_sha != pending.get("head_sha")
        or base_sha != pending.get("base_sha")
    ):
        raise RuntimeError("Pending PR identity or base changed; refusing automatic recovery.")
    if current.get("draft") is True:
        node_id = current.get("node_id")
        if not isinstance(node_id, str):
            raise RuntimeError("Cannot safely convert the pending draft PR to ready-for-review.")
        transition = graphql(token, """
          mutation($pullRequestId: ID!) {
            convertPullRequestToReadyForReview(input: {pullRequestId: $pullRequestId}) {
              pullRequest { isDraft }
            }
          }
        """, {"pullRequestId": node_id})
        ready = (transition.get("convertPullRequestToReadyForReview") or {}).get("pullRequest") or {}
        if ready.get("isDraft") is not False:
            raise RuntimeError("GitHub did not confirm that the pending PR is ready for owner review.")
    provisional_url = current.get("html_url")
    if not isinstance(provisional_url, str) or provisional_url != f"https://github.com/{repo}/pull/{number}":
        raise RuntimeError("Pending PR URL is invalid; preserving the existing task state.")
    save_pending_pr({
        "number": number,
        "url": provisional_url,
        "head_sha": head_sha,
        "base_sha": base_sha,
        "branch": branch,
        "run_id": str(pending.get("run_id") or run_id),
        "task": int(pending.get("task") or task),
    }, repo)
    dispatch_and_wait_all(
        token, repo, ("tests.yml", "browser-e2e.yml", "dependency-audit.yml"), branch, head_sha
    )
    refreshed = api(token, repo, "GET", f"pulls/{number}")
    main = api(token, repo, "GET", "branches/main")
    if (
        (refreshed.get("head") or {}).get("sha") != head_sha
        or (refreshed.get("base") or {}).get("sha") != base_sha
        or refreshed.get("draft") is not False
        or refreshed.get("state") != "open"
        or refreshed.get("merged") is True
        or (main.get("commit") or {}).get("sha") != base_sha
    ):
        raise RuntimeError("Pending PR changed during revalidation; it remains unaccepted.")
    url = refreshed.get("html_url")
    if not isinstance(url, str) or url != f"https://github.com/{repo}/pull/{number}":
        raise RuntimeError("GitHub returned an unexpected pull request URL.")
    save_pending_pr({
        "number": number,
        "url": url,
        "head_sha": head_sha,
        "base_sha": base_sha,
        "branch": branch,
        "run_id": str(pending.get("run_id") or run_id),
        "task": int(pending.get("task") or task),
    }, repo)
    return {
        "number": number,
        "url": url,
        "head_sha": head_sha,
        "base_sha": base_sha,
        "validated": True,
        "merged": False,
        "awaiting_review": True,
        "draft": False,
    }


def complete_merged_pr(token: str, repo: str, pending: dict[str, Any], *, api: Callable[..., Any], dispatch_and_wait_all: Callable[..., Any], clear_checkpoint: Callable[..., Any], clear_pending_state: Callable[..., Any]) -> dict[str, Any]:
    number = pending.get("number")
    if not isinstance(number, int) or isinstance(number, bool):
        raise RuntimeError("The pending autonomous PR has no valid number.")
    current = api(token, repo, "GET", f"pulls/{number}")
    head = current.get("head") or {}
    base = current.get("base") or {}
    merge_sha = current.get("merge_commit_sha")
    merged_by = current.get("merged_by") if isinstance(current.get("merged_by"), dict) else {}
    merged_by_login = merged_by.get("login")
    merged_by_type = merged_by.get("type")
    if (
        current.get("state") != "closed"
        or current.get("merged") is not True
        or not isinstance(merged_by_login, str)
        or not merged_by_login.strip()
        or merged_by_type != "User"
        or merged_by_login.endswith("[bot]")
        or (current.get("user") or {}).get("login") != "github-actions[bot]"
        or (head.get("repo") or {}).get("full_name") != repo
        or base.get("ref") != "main"
        or base.get("sha") != merge_sha
        or head.get("ref") != pending.get("branch")
        or head.get("sha") != pending.get("head_sha")
        or current.get("html_url") != pending.get("url")
        or not isinstance(merge_sha, str)
        or not re.fullmatch(r"[0-9a-f]{40}", merge_sha)
    ):
        raise RuntimeError("The saved PR was not merged as the exact reviewed candidate; task state is preserved.")
    main = api(token, repo, "GET", "branches/main")
    if (main.get("commit") or {}).get("sha") != merge_sha:
        raise RuntimeError("main moved after the reviewed PR merge; refusing to advance the engineering queue.")
    dispatch_and_wait_all(
        token, repo, ("tests.yml", "browser-e2e.yml", "dependency-audit.yml"), "main", merge_sha
    )
    confirmed = api(token, repo, "GET", f"pulls/{number}")
    main_after_merge = api(token, repo, "GET", "branches/main")
    if (
        confirmed.get("merged") is not True
        or confirmed.get("merge_commit_sha") != merge_sha
        or (confirmed.get("head") or {}).get("sha") != pending.get("head_sha")
        or (main_after_merge.get("commit") or {}).get("sha") != merge_sha
    ):
        raise RuntimeError("Post-merge validation changed or main no longer points to the reviewed merge.")
    clear_checkpoint()
    clear_pending_state()
    return {
        "number": number,
        "url": pending.get("url"),
        "head_sha": pending.get("head_sha"),
        "merge_sha": merge_sha,
        "merged": True,
        "post_merge_validated": True,
    }


def publish_pull_request(
    token: str, repo: str, branch: str, task_index: int, run_id: str,
    head_sha: str, base_sha: str,
    *, api: Callable[..., Any], checked: Callable[..., str], git_auth_env: Callable[[str], dict[str, str]],
    durable_state_lock: Callable[..., Any], state_directory: Callable[[], Any],
    save_pending_pr_unlocked: Callable[..., Any], dispatch_and_wait_all: Callable[..., Any],
) -> dict[str, Any]:
    checked(["git", "push", "origin", f"HEAD:refs/heads/{branch}"], env=git_auth_env(token))
    owner = repo.split("/", 1)[0]

    # Serialize remote PR creation with manual-task persistence. Persist the
    # durable PR owner record immediately, before independent CI dispatch.
    with durable_state_lock(state_directory()):
        existing = api(
            token, repo, "GET",
            f"pulls?state=open&head={quote(owner + ':' + branch, safe='')}&base=main&per_page=5",
        )
        if not isinstance(existing, list):
            raise RuntimeError("GitHub returned an invalid pull request lookup.")
        pr = existing[0] if existing else api(token, repo, "POST", "pulls", {
            "title": f"Autonomous engineering task {task_index} (run {run_id})",
            "head": branch,
            "base": "main",
            "draft": False,
            "body": (
                f"## Autonomous engineering task {task_index}\n\n"
                f"- Workflow run: https://github.com/{repo}/actions/runs/{run_id}\n"
                f"- Candidate commit: {head_sha}\n"
                "- Windows full test suite and repository quality gates passed before publication.\n"
                "- GitHub tests, browser E2E, and dependency audit are dispatched against this exact PR head.\n"
                "- The PR remains open for owner review; the controller never merges PRs automatically.\n"
                "- After the owner merges it, the controller verifies the exact merge SHA and post-merge CI before accepting the task or advancing the queue.\n"
                "- No deployment is performed.\n"
            ),
        })
        number = pr.get("number")
        pr_head = (pr.get("head") or {}).get("sha")
        pr_head_repository = ((pr.get("head") or {}).get("repo") or {}).get("full_name")
        pr_base = (pr.get("base") or {}).get("ref")
        pr_base_sha = (pr.get("base") or {}).get("sha")
        pr_author = (pr.get("user") or {}).get("login")
        if (
            not isinstance(number, int)
            or pr_author != "github-actions[bot]"
            or pr_head_repository != repo
        ):
            raise RuntimeError("Refusing publication: PR was not created by the trusted controller in this repository.")
        if (
            not isinstance(pr_head, str)
            or pr_head != head_sha
            or pr_base != "main"
            or pr_base_sha != base_sha
            or pr.get("draft") is not False
        ):
            raise RuntimeError("The PR head or main base changed before publication could be validated.")
        url = pr.get("html_url")
        if not isinstance(url, str) or url != f"https://github.com/{repo}/pull/{number}":
            raise RuntimeError("GitHub returned an unexpected pull request URL.")
        pending_state = {
            "number": number,
            "url": url,
            "head_sha": head_sha,
            "base_sha": base_sha,
            "branch": branch,
            "run_id": run_id,
            "task": task_index,
        }
        save_pending_pr_unlocked(pending_state, repo)

    dispatch_and_wait_all(
        token, repo, ("tests.yml", "browser-e2e.yml", "dependency-audit.yml"), branch, pr_head
    )
    refreshed_pr = api(token, repo, "GET", f"pulls/{number}")
    refreshed_head = (refreshed_pr.get("head") or {}).get("sha")
    if refreshed_head != head_sha:
        raise RuntimeError("The PR head changed during exact-SHA validation; preserving the task for revalidation.")
    if refreshed_pr.get("merged") is True:
        return {**pending_state, "validated": True, "merged": True, "owner_merged": True}
    if refreshed_pr.get("state") != "open" or refreshed_pr.get("draft") is not False:
        raise RuntimeError("The PR was closed or changed before owner-review handoff; preserving the task.")
    current_main = api(token, repo, "GET", "branches/main")
    refreshed_base = (refreshed_pr.get("base") or {}).get("sha")
    current_main_sha = (current_main.get("commit") or {}).get("sha")
    if refreshed_base != base_sha or current_main_sha != base_sha:
        raise RuntimeError(
            "The PR head or main base changed during validation; the PR remains open and must be revalidated."
        )
    return {
        **pending_state,
        "validated": True,
        "merged": False,
        "awaiting_review": True,
        "draft": False,
    }
