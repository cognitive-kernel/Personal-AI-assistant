import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.autonomous_engineering_pr_preflight import has_open_autonomous_pr


def _run_with_payload(payload, returncode=0):
    calls = []

    def run(command, *, timeout):
        calls.append((command, timeout))
        return SimpleNamespace(
            returncode=returncode,
            stdout=json.dumps(payload),
            stderr="",
        )

    return run, calls


def test_pr_preflight_detects_only_controller_owned_open_prs():
    repository = "owner/repository"
    run, calls = _run_with_payload([
        {
            "state": "open",
            "user": {"login": "github-actions[bot]"},
            "head": {
                "ref": "codex/local-engineering-123",
                "repo": {"full_name": repository},
            },
            "base": {"ref": "main"},
        }
    ])

    assert has_open_autonomous_pr("gh", repository=repository, run_command=run) is True
    assert calls == [(["gh", "api", f"repos/{repository}/pulls?state=open&per_page=100"], 12)]


def test_pr_preflight_ignores_unrelated_or_closed_prs():
    repository = "owner/repository"
    run, _ = _run_with_payload([
        {
            "state": "open",
            "user": {"login": "someone-else"},
            "head": {
                "ref": "codex/local-engineering-123",
                "repo": {"full_name": repository},
            },
            "base": {"ref": "main"},
        },
        {
            "state": "closed",
            "user": {"login": "github-actions[bot]"},
            "head": {
                "ref": "codex/local-engineering-124",
                "repo": {"full_name": repository},
            },
            "base": {"ref": "main"},
        },
    ])

    assert has_open_autonomous_pr("gh", repository=repository, run_command=run) is False


@pytest.mark.parametrize(
    ("payload", "returncode", "expected_status"),
    [
        ([], 1, 503),
        ("not-json", 0, 502),
        ([{"user": []}], 0, 502),
    ],
)
def test_pr_preflight_fails_closed_for_unverifiable_github_state(payload, returncode, expected_status):
    if payload == "not-json":
        run = lambda *_args, **_kwargs: SimpleNamespace(returncode=0, stdout=payload)
    else:
        run, _ = _run_with_payload(payload, returncode=returncode)

    with pytest.raises(HTTPException) as exc:
        has_open_autonomous_pr("gh", repository="owner/repository", run_command=run)

    assert exc.value.status_code == expected_status
