from __future__ import annotations

import argparse
import base64
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from urllib.parse import quote
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
ALLOWED = ("app/", "tests/", "docs/", "scripts/")
RUNTIME_FILES = {".agent-request.json", ".agent-github-state.json", ".agent-progress.json"}
PROTECTED = {
    ".github/workflows/autonomous-codex-engineering.yml",
    "scripts/autonomous_engineering_cycle.py",
    "scripts/autonomous_engineering_progress.py",
}
MARKERS = re.compile(r"(?m)^(TASK_COMPLETE|QUEUE_EMPTY|TASK_BLOCKED)\s*$")
CYCLE_LIMIT = 6 * 60 * 60


def run(command: list[str], *, env: dict[str, str] | None = None, capture: bool = False) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command, cwd=ROOT, env=env, text=True, encoding="utf-8", errors="replace",
        check=False, stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
    )


def checked(command: list[str], *, env: dict[str, str] | None = None) -> str:
    result = run(command, env=env, capture=True)
    if result.returncode:
        raise RuntimeError(
            f"Command failed ({result.returncode}): {command[0]}\n"
            f"{(result.stderr or result.stdout or '')[-4000:]}"
        )
    return (result.stdout or "").strip()


def record(
    run_id: str, attempt: str, phase: str, label: str, activity: str,
    *, status: str = "running",
) -> None:
    checked([
        sys.executable, "scripts/autonomous_engineering_progress.py", "record",
        "--run-id", run_id, "--run-attempt", attempt,
        "--phase", phase, "--label", label, "--activity", activity,
        "--status", status,
    ])

def snapshot(run_id: str, attempt: str) -> None:
    checked([
        sys.executable, "scripts/autonomous_engineering_progress.py", "snapshot",
        "--run-id", run_id, "--run-attempt", attempt,
    ])


def task_marker(report: Path) -> str:
    try:
        matches = MARKERS.findall(report.read_text(encoding="utf-8-sig"))
    except OSError as exc:
        raise RuntimeError("Codex did not produce its final task report.") from exc
    if len(matches) != 1:
        raise RuntimeError("Codex report must contain exactly one valid completion marker.")
    return matches[0]


def changed_paths() -> list[str]:
    unstaged = checked(["git", "diff", "--name-only", "-z"]).split("\0")
    staged = checked(["git", "diff", "--cached", "--name-only", "-z"]).split("\0")
    untracked = checked(["git", "ls-files", "--others", "--exclude-standard", "-z"]).split("\0")
    all_paths = unstaged + staged + untracked
    return sorted({p.replace("\\", "/") for p in all_paths if p and p.replace("\\", "/") not in RUNTIME_FILES})


def validate_paths(paths: list[str]) -> None:
    invalid = []
    for value in paths:
        path = Path(value)
        if (
            value.startswith("/") or "\\" in value
            or any(part in {"", ".", "..", ".git"} for part in value.split("/"))
            or not value.startswith(ALLOWED) or value in PROTECTED
            or path.is_symlink()
            or not (ROOT / path).resolve().is_relative_to(ROOT.resolve())
        ):
            invalid.append(value)
    if invalid:
        raise RuntimeError("Task changed protected or non-allowlisted paths: " + repr(invalid))


def prepare_commit(task_index: int) -> str:
    validate_paths(changed_paths())
    checked(["git", "add", "-A", "--", "app", "tests", "docs", "scripts"])
    staged = checked(["git", "diff", "--cached", "--no-renames", "--name-only", "-z"]).split("\0")
    staged = [p.replace("\\", "/") for p in staged if p]
    validate_paths(staged)
    if not staged:
        raise RuntimeError("Task reported completion but produced no reviewable changes.")
    checked(["git", "diff", "--cached", "--check"])
    records = checked(["git", "diff", "--cached", "--no-renames", "--numstat", "-z"]).split("\0")
    for item in records:
        if item:
            fields = item.split("\t", 2)
            if len(fields) != 3 or fields[0] == "-" or fields[1] == "-":
                raise RuntimeError("Binary changes are not allowed in autonomous task commits.")
    checked(["git", "config", "user.name", "github-actions[bot]"])
    checked(["git", "config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com"])
    checked(["git", "commit", "-m", f"chore: complete autonomous engineering task {task_index}"])
    return checked(["git", "rev-parse", "HEAD"])


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

def dispatch_and_wait_all(token: str, repo: str, workflows: tuple[str, ...], branch: str, expected_sha: str) -> None:
    dispatched_at = time.time()
    for workflow in workflows:
        api(token, repo, "POST", f"actions/workflows/{workflow}/dispatches", {"ref": branch})
    deadline = time.monotonic() + 20 * 60
    query_branch = quote(branch, safe="")
    pending = set(workflows)
    while pending and time.monotonic() < deadline:
        for workflow in tuple(pending):
            response = api(
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


def find_open_autonomous_pr(token: str, repo: str) -> dict[str, Any] | None:
    pulls = api(token, repo, "GET", "pulls?state=open&per_page=100")
    if not isinstance(pulls, list):
        raise RuntimeError("GitHub returned an invalid open pull request listing.")
    for item in pulls:
        if not isinstance(item, dict):
            continue
        head = item.get("head") or {}
        base = item.get("base") or {}
        branch = head.get("ref")
        if (
            isinstance(branch, str)
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


def complete_pending_pr(token: str, repo: str, pending: dict[str, Any]) -> dict[str, Any]:
    number = pending.get("number")
    if not isinstance(number, int):
        raise RuntimeError("The pending autonomous PR has no valid number.")
    current = api(token, repo, "GET", f"pulls/{number}")
    head = current.get("head") or {}
    base = current.get("base") or {}
    branch = head.get("ref")
    head_sha = head.get("sha")
    base_sha = base.get("sha")
    if (
        current.get("state") != "open"
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
            raise RuntimeError("GitHub did not confirm that the pending PR is ready for automatic validation.")
    dispatch_and_wait_all(
        token, repo, ("tests.yml", "browser-e2e.yml", "dependency-audit.yml"), branch, head_sha
    )
    refreshed = api(token, repo, "GET", f"pulls/{number}")
    main = api(token, repo, "GET", "branches/main")
    if (
        (refreshed.get("head") or {}).get("sha") != head_sha
        or (refreshed.get("base") or {}).get("sha") != base_sha
        or refreshed.get("draft") is not False
        or (main.get("commit") or {}).get("sha") != base_sha
    ):
        raise RuntimeError("Pending PR changed during revalidation; refusing automatic merge.")
    merged = api(token, repo, "PUT", f"pulls/{number}/merge", {
        "sha": head_sha,
        "merge_method": "squash",
    })
    if not isinstance(merged, dict) or merged.get("merged") is not True:
        raise RuntimeError(f"GitHub did not confirm recovery merge of PR #{number}.")
    confirmed = api(token, repo, "GET", f"pulls/{number}")
    if confirmed.get("merged") is not True:
        raise RuntimeError(f"GitHub did not confirm merged state for PR #{number}.")
    merge_sha = merged.get("sha")
    if not isinstance(merge_sha, str) or not re.fullmatch(r"[0-9a-f]{40}", merge_sha):
        raise RuntimeError("GitHub returned an invalid recovery merge SHA.")
    return {"number": number, "url": pending.get("url"), "head_sha": head_sha, "merge_sha": merge_sha, "merged": True}


def publish_pull_request(
    token: str, repo: str, branch: str, task_index: int, run_id: str,
    head_sha: str, base_sha: str,
) -> dict[str, Any]:
    env = git_auth_env(token)
    checked(["git", "push", "origin", f"HEAD:refs/heads/{branch}"], env=env)
    owner = repo.split("/", 1)[0]
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
            f"## Autonomous engineering task {task_index}\\n\\n"
            f"- Workflow run: https://github.com/{repo}/actions/runs/{run_id}\\n"
            f"- Candidate commit: {head_sha}\\n"
            "- Windows full test suite and repository quality gates passed before publication.\\n"
            "- GitHub tests, browser E2E, and dependency audit are dispatched against this exact PR head.\\n"
            "- The trusted controller merges this PR automatically after exact-SHA validation succeeds.\\n"
            "- No deployment is performed.\\n"
        ),
    })
    number = pr.get("number")
    pr_head = (pr.get("head") or {}).get("sha")
    pr_base = (pr.get("base") or {}).get("ref")
    pr_base_sha = (pr.get("base") or {}).get("sha")
    if (
        not isinstance(number, int)
        or not isinstance(pr_head, str)
        or pr_head != head_sha
        or pr_base != "main"
        or pr_base_sha != base_sha
        or pr.get("draft") is not False
    ):
        raise RuntimeError(
            "Refusing publication: draft PR head or exact main base does not match the validated candidate."
        )

    dispatch_and_wait_all(
        token, repo, ("tests.yml", "browser-e2e.yml", "dependency-audit.yml"), branch, pr_head
    )
    refreshed_pr = api(token, repo, "GET", f"pulls/{number}")
    current_main = api(token, repo, "GET", "branches/main")
    refreshed_head = (refreshed_pr.get("head") or {}).get("sha")
    refreshed_base = (refreshed_pr.get("base") or {}).get("sha")
    current_main_sha = (current_main.get("commit") or {}).get("sha")
    if refreshed_head != head_sha or refreshed_base != base_sha or current_main_sha != base_sha:
        raise RuntimeError(
            "The PR head or main base changed during validation; the draft PR remains open and must be revalidated."
        )
    url = refreshed_pr.get("html_url")
    if not isinstance(url, str) or url != f"https://github.com/{repo}/pull/{number}":
        raise RuntimeError("GitHub returned an unexpected pull request URL.")

    merged = api(token, repo, "PUT", f"pulls/{number}/merge", {
        "sha": head_sha,
        "merge_method": "squash",
    })
    if not isinstance(merged, dict) or merged.get("merged") is not True:
        raise RuntimeError(f"GitHub did not confirm merge of validated PR #{number}.")
    confirmed_pr = api(token, repo, "GET", f"pulls/{number}")
    if not isinstance(confirmed_pr, dict) or confirmed_pr.get("merged") is not True:
        raise RuntimeError(f"GitHub merge confirmation is missing for PR #{number}.")
    merge_sha = merged.get("sha")
    if not isinstance(merge_sha, str) or not re.fullmatch(r"[0-9a-f]{40}", merge_sha):
        raise RuntimeError(f"GitHub returned an invalid merge commit for PR #{number}.")
    main_after_merge = api(token, repo, "GET", "branches/main")
    if (main_after_merge.get("commit") or {}).get("sha") != merge_sha:
        raise RuntimeError("The merge was accepted but main does not point to the confirmed merge commit.")

    return {
        "number": number,
        "url": url,
        "head_sha": head_sha,
        "base_sha": base_sha,
        "merge_sha": merge_sha,
        "merged": True,
        "draft": False,
    }


def publish_pr_subprocess(
    token: str, repo: str, branch: str, task: int, run_id: str, sha: str, base_sha: str,
) -> dict[str, Any]:
    env = os.environ.copy()
    env.update({"GH_TOKEN": token, "GH_REPO": repo})
    result = run([
        sys.executable, str(Path(__file__).resolve()), "publish",
        "--branch", branch, "--task", str(task), "--run-id", run_id,
        "--sha", sha, "--base-sha", base_sha,
    ], env=env, capture=True)
    if result.returncode:
        raise RuntimeError(
            "Per-task draft PR publication failed: " + (result.stderr or result.stdout or "")[-4000:]
        )
    try:
        value = json.loads((result.stdout or "").strip().splitlines()[-1])
    except (ValueError, IndexError) as exc:
        raise RuntimeError("The PR publisher did not return valid JSON metadata.") from exc
    if not isinstance(value, dict):
        raise RuntimeError("The PR publisher returned invalid metadata.")
    return value


def publish_command(args: argparse.Namespace) -> int:
    token = os.environ.pop("GH_TOKEN", "")
    repo = os.environ.pop("GH_REPO", "")
    if not token or not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
        raise RuntimeError("A valid workflow token and repository are required.")
    result = publish_pull_request(token, repo, args.branch, args.task, args.run_id, args.sha, args.base_sha)
    print(json.dumps(result, ensure_ascii=False))
    return 0

def run_cycle(args: argparse.Namespace) -> int:
    token = os.environ.pop("GH_TOKEN", "")
    repo = os.environ.get("GH_REPO", "")
    if not token or not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
        raise RuntimeError("A valid workflow token and repository are required.")

    pending_pr = find_open_autonomous_pr(token, repo)
    if pending_pr:
        record(
            args.run_id, args.run_attempt, "recovery",
            "بازیابی PR تأییدشدهٔ قبلی",
            f"اعتبارسنجی مجدد PR #{pending_pr.get('number')} و ادامه از آخرین تغییر منتشرشده",
        )
        recovered = complete_pending_pr(token, repo, pending_pr)
        record(
            args.run_id, args.run_attempt, "complete",
            f"وظیفهٔ بازیابی‌شده ادغام شد",
            f"PR #{recovered['number']} پس از اعتبارسنجی مجدد ادغام شد؛ merge={recovered['merge_sha'][:12]}",
            status="completed",
        )
        snapshot(args.run_id, args.run_attempt)
        if args.mode == "continuous":
            api(token, repo, "POST", "actions/workflows/autonomous-codex-engineering.yml/dispatches", {
                "ref": "main",
                "inputs": {"manual_task": ""},
            })
        return 0

    started = time.monotonic()
    request_file = ROOT / ".agent-request.json"
    task = 1
    if time.monotonic() - started >= CYCLE_LIMIT - 60:
        raise RuntimeError("The six-hour workflow limit leaves insufficient time to start a task.")

    branch = f"codex/local-engineering-{args.run_id}-{args.run_attempt}-task-{task}"
    checked(["git", "fetch", "origin", "main"], env=git_auth_env(token))
    checked(["git", "switch", "main"])
    checked(["git", "reset", "--hard", "origin/main"])
    base_sha = checked(["git", "rev-parse", "HEAD"])
    checked(["git", "switch", "-c", branch])
    record(args.run_id, args.run_attempt, "coding", f"وظیفهٔ مهندسی {task}", "شروع وظیفهٔ مهندسی از صف")

    report = Path(os.environ.get("RUNNER_TEMP", str(ROOT))) / f"codex-report-{task}.md"
    attempt = 0
    failure_feedback = ""
    trusted_head = base_sha
    published_pr: dict[str, Any] | None = None

    while True:
        if time.monotonic() - started >= CYCLE_LIMIT - 60:
            raise RuntimeError(
                f"Task {task} exhausted this runner window after {attempt} attempt(s); "
                "it was not published as verified."
            )
        attempt += 1
        active_prompt = Path(args.prompt)
        if failure_feedback:
            active_prompt = Path(os.environ.get("RUNNER_TEMP", str(ROOT))) / f"codex-retry-prompt-{task}-{attempt}.md"
            original = Path(args.prompt).read_text(encoding="utf-8")
            active_prompt.write_text(
                original + "\n\n## Required repair after failed verification\n"
                + "This is a fresh session for the SAME task. Do not select or start another task.\n"
                + failure_feedback + "\nFix the root cause, add or update regression tests, then report TASK_COMPLETE only after your own relevant tests pass.\n",
                encoding="utf-8",
            )
        report.unlink(missing_ok=True)
        record(
            args.run_id, args.run_attempt, "coding",
            f"وظیفهٔ {task}؛ تلاش {attempt}",
            f"اجرای نشست تازه برای همان وظیفه؛ تلاش {attempt}",
        )
        result = run([
            sys.executable, "scripts/autonomous_engineering_progress.py", "codex",
            "--run-id", args.run_id, "--run-attempt", args.run_attempt,
            "--model", args.model, "--prompt", str(active_prompt), "--report", str(report),
        ])
        if result.returncode:
            failure_feedback = (
                f"Attempt {attempt}: Codex exited with code {result.returncode}. "
                "Inspect the current worktree and repair the same task.\n"
            )
            continue

        marker = task_marker(report)
        if marker == "QUEUE_EMPTY":
            if attempt > 1:
                raise RuntimeError("Codex reported QUEUE_EMPTY while retrying a failed task; refusing to skip the task.")
            checked(["git", "switch", "main"])
            checked(["git", "branch", "-D", branch])
            print("Engineering queue is empty.")
            return 0
        if marker == "TASK_BLOCKED":
            raise RuntimeError(f"Codex reported task {task} as blocked; record the blocker and select no unsafe action.")
        if marker != "TASK_COMPLETE":
            failure_feedback = (
                f"Attempt {attempt}: Codex did not return TASK_COMPLETE. "
                "Correct the report and finish this same task.\n"
            )
            continue
        if checked(["git", "rev-parse", "HEAD"]) != trusted_head:
            raise RuntimeError("Codex must not create or rewrite commits; only the trusted controller may commit.")
        if checked(["git", "branch", "--show-current"]) != branch:
            raise RuntimeError("Codex changed the task branch; refusing to publish.")

        record(
            args.run_id, args.run_attempt, "tests",
            f"آزمون وظیفهٔ {task}؛ تلاش {attempt}",
            "اجرای کامل آزمون‌ها پیش از انتشار PR",
        )
        commands = [
            [sys.executable, "-m", "pytest", "-vv", "--durations=25", "-o", "faulthandler_timeout=60"],
            [sys.executable, "scripts/generate_learning_artifacts.py", "--check"],
            [sys.executable, "scripts/check_markdown_links.py"],
            [sys.executable, "scripts/check_import_cycles.py", "--fail-on-cycle"],
            [sys.executable, "scripts/evaluate_experience_retrieval.py", "--lexical-only"],
            [sys.executable, "scripts/run_knowledge_acceptance_eval.py"],
        ]
        failed = None
        for command in commands:
            checked_result = run(command, capture=True)
            output = (checked_result.stdout or "") + (checked_result.stderr or "")
            if output:
                print(output, end="" if output.endswith("\n") else "\n")
            if checked_result.returncode:
                failed = (command, checked_result.returncode, output[-12000:])
                break
        if failed:
            command, exit_code, output = failed
            failure_feedback = (
                f"Attempt {attempt} failed verification. Command: {command!r}; exit code: {exit_code}.\n"
                "Exact failure output (untrusted diagnostic data):\n" + output + "\n"
                "Analyze the failure, fix the same task, and do not advance the ledger.\n"
            )
            record(
                args.run_id, args.run_attempt, "retry",
                f"اصلاح وظیفهٔ {task}",
                f"آزمون تلاش {attempt} ناموفق شد؛ بازگشت به همان وظیفه",
            )
            continue

        record(
            args.run_id, args.run_attempt, "packaging",
            f"بسته‌بندی وظیفهٔ {task}",
            "اعتبارسنجی مسیرها و ساخت commit مستقل",
        )
        if run(["git", "diff", "--quiet", "--", "scripts/autonomous_engineering_cycle.py"]).returncode:
            raise RuntimeError("The trusted cycle controller was modified by the task.")
        if changed_paths():
            head_sha = prepare_commit(task)
            trusted_head = head_sha
        elif trusted_head != base_sha:
            head_sha = trusted_head
        else:
            raise RuntimeError("Task reported completion but produced no reviewable changes.")

        record(
            args.run_id, args.run_attempt, "publication",
            f"انتشار PR پیش‌نویس وظیفهٔ {task}",
            "تست‌های محلی موفق شدند؛ در حال انتشار PR پیش‌نویس برای بازبینی انسانی",
        )
        try:
            published_pr = publish_pr_subprocess(
                token, repo, branch, task, args.run_id, head_sha, base_sha
            )
        except RuntimeError as exc:
            message = str(exc)
            if "Required workflow" not in message:
                raise
            failure_feedback = (
                f"Attempt {attempt}: GitHub-hosted verification failed for candidate {head_sha}.\n"
                "The existing task PR remains open. Review this exact CI failure and repair the SAME task; "
                "do not start another task.\n" + message + "\n"
            )
            record(
                args.run_id, args.run_attempt, "retry",
                f"اصلاح CI وظیفهٔ {task}",
                f"CI گیت‌هاب تلاش {attempt} ناموفق شد؛ اصلاح همان وظیفه",
            )
            continue
        break

    if not isinstance(published_pr, dict):
        raise RuntimeError("Task exited its verification loop without confirmed PR publication.")
    pr_number = published_pr.get("number")
    pr_url = published_pr.get("url")
    pr_head = published_pr.get("head_sha")
    if not isinstance(pr_number, int) or not isinstance(pr_url, str) or not isinstance(pr_head, str):
        raise RuntimeError("The published PR metadata is incomplete.")

    if published_pr.get("merged") is not True:
        raise RuntimeError("The candidate PR was not confirmed as merged; the engineering queue cannot advance.")
    merge_sha = published_pr.get("merge_sha")
    record(
        args.run_id, args.run_attempt, "complete",
        f"وظیفهٔ {task} ادغام شد",
        f"PR #{pr_number} با موفقیت و پس از اعتبارسنجی کامل در main ادغام شد؛ merge={str(merge_sha)[:12]}",
        status="completed",
    )
    snapshot(args.run_id, args.run_attempt)
    request_file.unlink(missing_ok=True)
    if args.mode == "continuous":
        api(token, repo, "POST", "actions/workflows/autonomous-codex-engineering.yml/dispatches", {
            "ref": "main",
            "inputs": {"manual_task": ""},
        })
        print(f"Task {task} merged as PR #{pr_number}; the next continuous cycle was dispatched from main: {pr_url}")
    else:
        print(f"Task {task} merged as PR #{pr_number}; scheduled mode will resume at its next configured interval: {pr_url}")
    return 0

def main() -> int:
    parser = argparse.ArgumentParser()
    subs = parser.add_subparsers(dest="command", required=True)
    cycle = subs.add_parser("run")
    cycle.add_argument("--run-id", required=True)
    cycle.add_argument("--run-attempt", default="1")
    cycle.add_argument("--model", required=True)
    cycle.add_argument("--prompt", required=True)
    cycle.add_argument("--mode", choices=("scheduled", "continuous"), required=True)
    pub = subs.add_parser("publish")
    pub.add_argument("--branch", required=True)
    pub.add_argument("--task", required=True, type=int)
    pub.add_argument("--run-id", required=True)
    pub.add_argument("--sha", required=True)
    pub.add_argument("--base-sha", required=True)
    args = parser.parse_args()
    return publish_command(args) if args.command == "publish" else run_cycle(args)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"Autonomous engineering cycle failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(1)
