from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_autonomous_workflow_bootstraps_python_and_node_before_using_them():
    workflow = (ROOT / ".github" / "workflows" / "autonomous-codex-engineering.yml").read_text(
        encoding="utf-8"
    )

    python_bootstrap = workflow.index(
        "Bootstrap project-local managed Python runtime for this Runner job"
    )
    node_setup = workflow.index(
        "Provision Node.js runtime for this Runner job"
    )
    python_use = workflow.index(
        "Resolve managed Python runtime under the Runner service identity"
    )
    python_venv = workflow.index(
        "Prepare Runner virtual environment and install runtime dependencies"
    )
    codex_install = workflow.index(
        "Ensure Codex CLI is installed for the Runner service account"
    )
    gh_setup = workflow.index(
        "Provision verified GitHub CLI for this Runner job"
    )

    assert python_bootstrap < python_use < python_venv
    assert node_setup < codex_install
    assert gh_setup < codex_install
    assert '& $bootstrap -RuntimeOnly' in workflow
    assert 'PAA_VENV_DIR=$selectedVenv' in workflow
    assert 'node-version: "22"' in workflow
    assert "actions/setup-python@" not in workflow
    assert "Use preinstalled system Python" not in workflow
    assert "Node.js must be installed system-wide" not in workflow
    assert "Install Node.js LTS system-wide" not in workflow
    assert '$requiredGhVersion = "2.104.0"' in workflow
    assert "Get-FileHash -LiteralPath $archivePath -Algorithm SHA256" in workflow
    assert "official GitHub CLI checksum file" in workflow


def test_autonomous_workflow_keeps_codex_installation_pinned():
    workflow = (ROOT / ".github" / "workflows" / "autonomous-codex-engineering.yml").read_text(
        encoding="utf-8"
    )

    assert 'requiredCodexVersion = "0.167.0"' in workflow
    assert 'npm install --global "@openai/codex@$requiredCodexVersion"' in workflow


def test_autonomous_workflow_uses_the_selected_managed_python_for_execution():
    workflow = (ROOT / ".github" / "workflows" / "autonomous-codex-engineering.yml").read_text(
        encoding="utf-8"
    )

    assert '$venvPython = Join-Path $env:PAA_VENV_DIR "Scripts\\python.exe"' in workflow
    assert '& $venvPython "$env:GITHUB_WORKSPACE\\scripts\\autonomous_engineering_cycle.py" run' in workflow
    assert 'Join-Path $env:GITHUB_WORKSPACE ".venv\\Scripts\\python.exe"' not in workflow
    assert '$env:PATH = "$env:GITHUB_WORKSPACE\\.venv\\Scripts;$env:PATH"' not in workflow
