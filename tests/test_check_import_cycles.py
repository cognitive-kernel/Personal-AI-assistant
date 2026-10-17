from __future__ import annotations

from pathlib import Path

from scripts.check_import_cycles import build_import_graph, find_import_cycles


def test_module_time_import_cycle_is_detected(tmp_path: Path) -> None:
    package = tmp_path / "app"
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "alpha.py").write_text("from . import beta\n", encoding="utf-8")
    (package / "beta.py").write_text("from . import alpha\n", encoding="utf-8")

    graph = build_import_graph(package)

    assert find_import_cycles(graph) == [["app.alpha", "app.beta"]]


def test_function_local_import_does_not_create_eager_cycle(tmp_path: Path) -> None:
    package = tmp_path / "app"
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "alpha.py").write_text(
        "def use_beta():\n    from . import beta\n", encoding="utf-8"
    )
    (package / "beta.py").write_text("from . import alpha\n", encoding="utf-8")

    graph = build_import_graph(package)

    assert "app.beta" not in graph["app.alpha"]
    assert find_import_cycles(graph) == []


def test_class_body_import_is_still_treated_as_module_time(tmp_path: Path) -> None:
    package = tmp_path / "app"
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "alpha.py").write_text(
        "class UsesBeta:\n    from . import beta\n", encoding="utf-8"
    )
    (package / "beta.py").write_text("from . import alpha\n", encoding="utf-8")

    graph = build_import_graph(package)

    assert find_import_cycles(graph) == [["app.alpha", "app.beta"]]
