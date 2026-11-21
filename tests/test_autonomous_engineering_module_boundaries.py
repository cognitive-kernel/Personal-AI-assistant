from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "app" / "static"


def _read(name: str) -> str:
    return (STATIC / name).read_text(encoding="utf-8")


def test_autonomous_engineering_scripts_load_in_dependency_order():
    page = _read("autonomous-engineering.html")
    ordered = [
        "autonomous-engineering-core.js",
        "autonomous-engineering-runs.js",
        "autonomous-engineering-status.js",
        "autonomous-engineering-controls.js",
        "autonomous-engineering-progress.js",
        "autonomous-engineering.js",
    ]
    positions = [page.index(f'src="/static/{name}"') for name in ordered]
    assert positions == sorted(positions)
    for name in ordered:
        assert f'<script src="/static/{name}" defer></script>' in page


def test_autonomous_engineering_features_have_versioned_single_owner_contracts():
    core = _read("autonomous-engineering-core.js")
    status = _read("autonomous-engineering-status.js")
    controls = _read("autonomous-engineering-controls.js")
    runs = _read("autonomous-engineering-runs.js")
    progress = _read("autonomous-engineering-progress.js")
    shell = _read("autonomous-engineering.js")

    assert "window.autonomousEngineeringCore = Object.freeze({version: 1" in core
    assert "window.autonomousEngineeringStatus = Object.freeze({" in status
    assert "window.autonomousEngineeringControls = Object.freeze({version: 1})" in controls
    assert "window.autonomousEngineeringRuns = Object.freeze({version: 1" in runs
    assert "window.autonomousEngineeringProgress = Object.freeze({version: 1" in progress

    assert "window.autonomousEngineeringCore" in status
    assert "window.autonomousEngineeringStatus" in controls
    assert "window.autonomousEngineeringCore" in runs
    assert "window.autonomousEngineeringCore" in progress
    assert len(shell.splitlines()) <= 40
    assert "fetch(" not in shell
    assert "addEventListener(" not in shell


def test_autonomous_engineering_status_refresh_and_controls_remain_isolated():
    status = _read("autonomous-engineering-status.js")
    controls = _read("autonomous-engineering-controls.js")

    assert "async function refreshStatus()" in status
    assert "statusRefreshPromise" in status
    assert "async function refreshAfterMutation()" in status
    assert "dispatchPendingUntil" in status
    assert "function canStartManualTask()" in status
    assert 'request("settings"' not in status
    assert 'request("dispatch"' not in status
    assert 'taskForm.addEventListener("submit"' in controls
    assert 'request("dispatch"' in controls
    assert "status.canStartManualTask()" in controls
    assert "pending_task: " in status
    assert "pending_owner: " in status


def test_autonomous_engineering_run_history_discards_stale_responses():
    runs = _read("autonomous-engineering-runs.js")
    assert "let runsRequestSequence = 0" in runs
    assert "const requestSequence = ++runsRequestSequence" in runs
    assert "if (requestSequence !== runsRequestSequence) return" in runs


def test_autonomous_engineering_progress_queues_user_refresh_and_rejects_stale_ranges():
    progress = _read("autonomous-engineering-progress.js")
    assert "let progressBusy = false" in progress
    assert "let progressRefreshQueued = false" in progress
    assert "const selectionIsCurrent = () =>" in progress
    assert "if (!selectionIsCurrent())" in progress
    assert "if (progressRefreshQueued)" in progress
    assert 'refreshProgress({queueIfBusy: true})' in progress


def test_autonomous_engineering_ui_has_no_cross_module_inner_html_rendering():
    for name in (
        "autonomous-engineering-core.js",
        "autonomous-engineering-status.js",
        "autonomous-engineering-controls.js",
        "autonomous-engineering-runs.js",
        "autonomous-engineering-progress.js",
        "autonomous-engineering.js",
    ):
        assert "innerHTML" not in _read(name)


def test_autonomous_engineering_selector_contract_matches_the_page():
    page = _read("autonomous-engineering.html")
    ids = set(re.findall(r'id="([^"]+)"', page))
    used = set()
    for name in (
        "autonomous-engineering-core.js",
        "autonomous-engineering-status.js",
        "autonomous-engineering-controls.js",
        "autonomous-engineering-runs.js",
        "autonomous-engineering-progress.js",
    ):
        source = _read(name)
        used.update(re.findall(r'\$\("([^"]+)"\)', source))
    assert not used - ids, f"UI module selectors missing from HTML: {sorted(used - ids)}"
