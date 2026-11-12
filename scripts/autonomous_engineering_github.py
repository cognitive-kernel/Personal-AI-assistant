"""GitHub transport and exact-SHA workflow polling for Autonomous Engineering."""

from __future__ import annotations

import base64
import json
import os
import time
import urllib.error
import urllib.request
from typing import Any, Callable
from urllib.parse import quote


def api(
    token: str, repo: str, method: str, path: str,
    payload: dict[str, Any] | None = None,
) -> Any:
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(
        f"https://api.github.com/repos/{repo}/{path.lstrip('/')}",
        data=body, method=method,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "Personal-AI-assistant-autonomous-engineering",
            **({"Content-Type": "application/json"} if body is not None else {}),
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            raw = response.read(1_000_000).decode("utf-8")
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read(4000).decode("utf-8", errors="replace")
        raise RuntimeError(f"GitHub API {method} {path} failed ({exc.code}): {detail}") from exc
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"GitHub API {method} {path} failed: {type(exc).__name__}") from exc


def dispatch_and_wait_all(token: str, repo: str, workflows: tuple[str, ...], branch: str, expected_sha: str, *, request: Callable[..., Any] | None = None) -> None:
    request_api = request or api
    dispatched_at = time.time()
    for workflow in workflows:
        request_api(token, repo, "POST", f"actions/workflows/{workflow}/dispatches", {"ref": branch})
    deadline = time.monotonic() + 20 * 60
    query_branch = quote(branch, safe="")
    pending = set(workflows)
    while pending and time.monotonic() < deadline:
        for workflow in tuple(pending):
            response = request_api(
                token, repo, "GET",
                f"actions/workflows/{workflow}/runs?branch={query_branch}&event=workflow_dispatch&per_page=20",
            )
            matches = [
                item for item in response.get("workflow_runs", [])
                if item.get("head_branch") == branch
                and item.get("head_sha") == expected_sha
                and item.get("event") == "workflow_dispatch"
                and item.get("created_at")
                and __import__("datetime").datetime.fromisoformat(
                    item["created_at"].replace("Z", "+00:00")
                ).timestamp() >= dispatched_at - 10
            ]
            if not matches:
                continue
            latest = max(matches, key=lambda item: item.get("run_number", 0))
            if latest.get("status") == "completed":
                if latest.get("conclusion") != "success":
                    raise RuntimeError(
                        f"Required workflow {workflow} failed for task commit {expected_sha}: "
                        f"{latest.get('html_url', '')} ({latest.get('conclusion')})"
                    )
                pending.remove(workflow)
        if pending:
            time.sleep(5)
    if pending:
        raise RuntimeError("Timed out waiting for required workflows: " + ", ".join(sorted(pending)))


def git_auth_env(token: str) -> dict[str, str]:
    env = os.environ.copy()
    encoded = base64.b64encode(f"x-access-token:{token}".encode()).decode("ascii")
    env.update({
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": "http.extraheader",
        "GIT_CONFIG_VALUE_0": f"AUTHORIZATION: basic {encoded}",
    })
    env.pop("GH_TOKEN", None)
    env.pop("GITHUB_TOKEN", None)
    return env


def graphql(token: str, query: str, variables: dict[str, Any]) -> dict[str, Any]:
    body = json.dumps({"query": query, "variables": variables}).encode("utf-8")
    request = urllib.request.Request(
        "https://api.github.com/graphql",
        data=body,
        method="POST",
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": "Personal-AI-assistant-autonomous-engineering",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = json.loads(response.read(1_000_000).decode("utf-8"))
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"GitHub GraphQL request failed: {type(exc).__name__}") from exc
    if payload.get("errors"):
        raise RuntimeError("GitHub rejected the pull request readiness transition.")
    data = payload.get("data")
    return data if isinstance(data, dict) else {}
