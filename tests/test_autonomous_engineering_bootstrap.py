from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_autonomous_workflow_bootstraps_python_and_node_before_using_them():
    workflow = (ROOT / ".github" / "workflows" / "autonomous-codex-engineering.yml").read_text(
        encoding="utf-8"
    )

    python_setup = workflow.index("actions/setup-python@6d4fd22b20465ccc0f7354476588e8df7c9ca0e0")
    node_setup = workflow.index("actions/setup-node@249970729cb0ef3589644e2896645e5dc5ba9c38")
    python_use = workflow.index("Resolve provisioned Python runtime under the Runner service identity")
    python_venv = workflow.index("Prepare Runner virtual environment and install runtime dependencies")
    codex_install = workflow.index("Ensure Codex CLI is installed for the Runner service account")

    assert python_setup < python_use < python_venv
    assert node_setup < codex_install
    assert 'python-version: "3.11"' in workflow
    assert 'node-version: "22"' in workflow
    assert "Use preinstalled system Python" not in workflow
    assert "Node.js must be installed system-wide" not in workflow
    assert "Install Node.js LTS system-wide" not in workflow


def test_autonomous_workflow_keeps_codex_installation_pinned():
    workflow = (ROOT / ".github" / "workflows" / "autonomous-codex-engineering.yml").read_text(
        encoding="utf-8"
    )

    assert 'requiredCodexVersion = "0.167.0"' in workflow
    assert 'npm install --global "@openai/codex@$requiredCodexVersion"' in workflow
