"""Authoritative, read-only GitHub admission before any durable task mutation.

Uses only the Python standard library and the dedicated Variables:read token.
No shell or service is needed by this control-plane step.
"""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable, Any


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise RuntimeError("Repository settings redirect refused.")


def read_settings(repository: str, token: str, *, opener=None) -> tuple[bool, str]:
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository) or ".." in repository or not token:
        raise RuntimeError("A valid repository and dedicated Variables: read token are required.")
    opener = opener or urllib.request.build_opener(NoRedirect())
    base = f"https://api.github.com/repos/{repository}"

    def get(path: str):
        request = urllib.request.Request(base + path, headers={
            "Accept": "application/vnd.github+json",
            "Authorization": "Bearer " + token,
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "Personal-AI-Assistant-Engineering",
        }, method="GET")
        with opener.open(request, timeout=15) as response:
            raw = response.read(1024 * 1024 + 1)
            if len(raw) > 1024 * 1024:
                raise RuntimeError("Settings response exceeds its limit.")
            value = json.loads(raw)
            if not isinstance(value, dict):
                raise RuntimeError("Settings response has an invalid shape.")
            return value

    def verify_inventory():
        value = get("/actions/variables?per_page=1")
        total = value.get("total_count")
        if type(total) is not int or total < 0 or not isinstance(value.get("variables"), list):
            raise RuntimeError("Could not verify Variables: read permission.")

    def variable(name: str, default: str, allowed: frozenset[str]) -> str:
        try:
            value = get("/actions/variables/" + name)
        except urllib.error.HTTPError as exc:
            if exc.code != 404:
                raise
            verify_inventory()
            return default
        result = value.get("value")
        if not isinstance(result, str) or result.strip().casefold() not in allowed:
            raise RuntimeError("Repository variable has an invalid value.")
        return result.strip().casefold()

    try:
        metadata = get("")
        identity = metadata.get("full_name")
        if not isinstance(identity, str) or identity.casefold() != repository.casefold():
            raise RuntimeError("Repository identity could not be verified.")
        verify_inventory()
        paused = variable("AUTONOMOUS_ENGINEERING_PAUSED", "false", frozenset({"true", "false"}))
        mode = variable("AUTONOMOUS_ENGINEERING_MODE", "continuous", frozenset({"continuous", "scheduled"}))
        return paused == "true", mode
    except (OSError, ValueError, TypeError) as exc:
        raise RuntimeError("Repository settings unavailable; no task will start.") from None


def admission_outputs(*, paused: bool, mode: str, event: str, manual_task: str, recovery_attempt: str, resume_once: str) -> dict[str, str]:
    if type(paused) is not bool or mode not in {"continuous", "scheduled"}:
        raise ValueError("Invalid live settings.")
    if event not in {"push", "schedule", "workflow_dispatch"} or resume_once not in {"true", "false"}:
        raise ValueError("Invalid event metadata.")
    if not re.fullmatch(r"[0-9]{1,9}", recovery_attempt):
        raise ValueError("Invalid recovery attempt.")
    if len(manual_task) > 2000 or "\x00" in manual_task:
        raise ValueError("Invalid manual task.")
    automatic = event in {"push", "schedule"} or int(recovery_attempt) > 0
    unmarked_empty = event == "workflow_dispatch" and not manual_task.strip() and resume_once != "true"
    skip = paused or (mode == "scheduled" and (automatic or unmarked_empty))
    return {"paused": str(paused).lower(), "skip": str(skip).lower(), "mode": mode}


def main() -> None:
    token = os.environ.pop("VARIABLES_TOKEN", "").strip()
    try:
        paused, mode = read_settings(os.environ.get("GITHUB_REPOSITORY", ""), token)
        clear_queue = os.environ.get("CLEAR_PENDING_TASKS", "false") == "true"
        outputs = admission_outputs(
            paused=paused, mode=mode, event=os.environ.get("EVENT_NAME", ""),
            manual_task=os.environ.get("MANUAL_TASK", ""),
            recovery_attempt=os.environ.get("RECOVERY_ATTEMPT", "0"),
            resume_once=os.environ.get("RESUME_ONCE", "false"),
        )
        if clear_queue:
            if os.environ.get("EVENT_NAME") != "workflow_dispatch" or os.environ.get("MANUAL_TASK") or os.environ.get("RESUME_ONCE") == "true":
                raise RuntimeError("Queue cleanup cannot be combined with task execution.")
            outputs["skip"] = "true"
    except (RuntimeError, ValueError) as exc:
        # Never print a remote response, raw task, credential or arbitrary URL.
        raise SystemExit("Live admission could not be verified; no task or state mutation will start.") from None
    finally:
        del token
    with Path(os.environ["GITHUB_OUTPUT"]).open("a", encoding="utf-8") as output:
        for key, value in outputs.items():
            output.write(f"{key}={value}\n")
    print("Live repository admission verified; skip=" + outputs["skip"])


if __name__ == "__main__":
    main()
