import ast
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_autonomous_engineering_state_broker_tests_do_not_shadow_pytest_cases():
    path = ROOT / "tests" / "test_autonomous_engineering_state_broker.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names = [
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name.startswith("test_")
    ]
    duplicates = sorted(name for name, count in Counter(names).items() if count > 1)

    assert not duplicates, (
        "Duplicate top-level pytest function names overwrite earlier tests "
        f"during module import: {duplicates}"
    )
