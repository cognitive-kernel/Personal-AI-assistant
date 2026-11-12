from __future__ import annotations

import ast
from pathlib import Path


def test_github_and_pull_request_modules_do_not_depend_on_cycle_orchestrator():
    root = Path(__file__).resolve().parents[1]
    module_paths = (
        root / "scripts" / "autonomous_engineering_github.py",
        root / "scripts" / "autonomous_engineering_pull_requests.py",
    )
    for path in module_paths:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                assert node.module not in {
                    "autonomous_engineering_cycle",
                    "scripts.autonomous_engineering_cycle",
                }
            elif isinstance(node, ast.Import):
                assert all(
                    alias.name not in {
                        "autonomous_engineering_cycle",
                        "scripts.autonomous_engineering_cycle",
                    }
                    for alias in node.names
                )


def test_cycle_orchestrator_retains_compatibility_facade_for_extracted_services():
    from scripts import autonomous_engineering_cycle as cycle

    assert callable(cycle.api)
    assert callable(cycle.dispatch_and_wait_all)
    assert callable(cycle.find_open_autonomous_pr)
    assert callable(cycle.complete_pending_pr)
    assert callable(cycle.complete_merged_pr)
    assert callable(cycle.publish_pull_request)
