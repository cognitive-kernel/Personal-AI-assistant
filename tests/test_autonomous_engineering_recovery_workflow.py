from pathlib import Path

import pytest

from scripts import autonomous_engineering_recovery as recovery


def test_recovery_workflow_checks_out_trusted_policy_and_invokes_it():
    workflow = (
        Path(__file__).resolve().parents[1]
        / ".github" / "workflows" / "autonomous-codex-recovery.yml"
    ).read_text(encoding="utf-8")

    assert "workflow_run:" in workflow
    assert "jobs:\n  recover:" in workflow
    assert "    steps:\n      - name: Check out trusted main recovery policy" in workflow
    assert "actions: write" in workflow
    assert "contents: read" in workflow
    assert "actions/checkout@" in workflow
    assert "ref: main" in workflow
    assert "persist-credentials: false" in workflow
    assert "python scripts/autonomous_engineering_recovery.py" in workflow
    assert "FAILED_RUN_ID:" in workflow
    assert "AUTONOMOUS_ENGINEERING_VARIABLES_TOKEN: ${{ secrets.AUTONOMOUS_ENGINEERING_VARIABLES_TOKEN }}" in workflow
    assert "github.event.workflow_run.head_branch == 'main'" in workflow
    policy = (
        Path(__file__).resolve().parents[1]
        / "scripts" / "autonomous_engineering_recovery.py"
    ).read_text(encoding="utf-8")
    assert "def _verify_repository_access(client: GitHubActionsClient, expected_repository: str)" in policy
    assert "_verify_repository_access(client, repository)" in policy
    assert "def _verify_variables_read_access(client: GitHubActionsClient)" in policy
    assert '"\/actions\/variables?per_page=1"' in policy
    assert "repository Variables read permission" in policy
    assert policy.index("_verify_repository_access(client, repository)") < policy.index("if _live_execution_mode(client)")
    assert policy.rindex("_verify_repository_access(client, repository)") > policy.index("time.sleep(delay_seconds)")
    assert "def _live_execution_mode(client: GitHubActionsClient)" in policy
    assert "def _live_pause_enabled(client: GitHubActionsClient)" in policy
    assert "def request_settings(" in policy
    assert "only GET requests are allowed" in policy
    assert "def _settings_request(" in policy
    assert "set_settings_token(settings_token)" in policy
    assert 'os.environ.pop("AUTONOMOUS_ENGINEERING_VARIABLES_TOKEN", "")' in policy
    assert '"/actions/variables/AUTONOMOUS_ENGINEERING_MODE"' in policy
    assert '"/actions/variables/AUTONOMOUS_ENGINEERING_PAUSED"' in policy
    assert "Recovery failed closed" in policy
    assert policy.index("if _live_execution_mode(client)") < policy.index("time.sleep(delay_seconds)")
    assert policy.rindex("if _live_execution_mode(client)") > policy.index("time.sleep(delay_seconds)")
    assert policy.rindex("if _live_pause_enabled(client)") > policy.index("time.sleep(delay_seconds)")
    assert "AUTONOMOUS_ENGINEERING_MODE:" not in workflow
    assert "AUTONOMOUS_ENGINEERING_PAUSED:" not in workflow


def test_recovery_workflow_does_not_hide_policy_or_dispatch_logic_inline():
    workflow = (
        Path(__file__).resolve().parents[1]
        / ".github" / "workflows" / "autonomous-codex-recovery.yml"
    ).read_text(encoding="utf-8")

    assert "CIRCUIT BREAKER OPEN" not in workflow
    assert "max_consecutive_failures" not in workflow
    assert "/dispatches" not in workflow


def test_recovery_policy_uses_bounded_backoff_and_a_finite_breaker():
    policy = (
        Path(__file__).resolve().parents[1]
        / "scripts" / "autonomous_engineering_recovery.py"
    ).read_text(encoding="utf-8")

    assert "MAX_CONSECUTIVE_FAILURES = 3" in policy
    assert "delay_seconds = min(240, 30 * (2 ** min(max(streak - 1, 0), 3)))" in policy
    assert "time.sleep(delay_seconds)" in policy
    assert "CIRCUIT BREAKER OPEN" in policy
    assert "durable task/checkpoint is preserved" in policy



def test_recovery_verifies_repository_access_before_defaulting_missing_variables():
    class Client:
        def __init__(self, metadata):
            self.metadata = metadata
            self.calls = []

        def request(self, method, path):
            self.calls.append((method, path))
            if path == "/":
                if isinstance(self.metadata, Exception):
                    raise self.metadata
                return self.metadata
            if path == "/actions/variables?per_page=1":
                return {"total_count": 0, "variables": []}
            if path == "/actions/variables/AUTONOMOUS_ENGINEERING_MODE":
                raise RuntimeError("GitHub Actions API failed: HTTP 404: Not Found")
            raise AssertionError((method, path))

    accessible = Client({"full_name": "Owner/Repo"})
    recovery._verify_repository_access(accessible, "owner/repo")
    assert recovery._live_execution_mode(accessible) == "continuous"
    assert accessible.calls == [
        ("GET", "/"),
        ("GET", "/actions/variables/AUTONOMOUS_ENGINEERING_MODE"),
        ("GET", "/actions/variables?per_page=1"),
    ]

    inaccessible = Client(RuntimeError("GitHub Actions API failed: HTTP 404: Not Found"))
    with pytest.raises(RuntimeError, match="verify the configured repository"):
        recovery._verify_repository_access(inaccessible, "owner/repo")
    assert inaccessible.calls == [("GET", "/")]
