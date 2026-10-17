from pathlib import Path

import pytest

from scripts.check_import_cycles import (
    build_import_graph,
    find_import_cycles,
    main,
)


def test_finds_multi_module_cycle_and_self_loop():
    graph = {
        "app.alpha": {"app.beta"},
        "app.beta": {"app.alpha", "app.gamma"},
        "app.gamma": set(),
        "app.self_import": {"app.self_import"},
    }

    assert find_import_cycles(graph) == [
        ["app.alpha", "app.beta"],
        ["app.self_import"],
    ]


def test_acyclic_graph_returns_no_cycles():
    assert find_import_cycles(
        {"app.main": {"app.db"}, "app.db": {"app.config"}, "app.config": set()}
    ) == []


def test_build_graph_resolves_absolute_and_relative_imports(tmp_path: Path):
    package = tmp_path / "app"
    package.mkdir()
    (package / "__init__.py").write_text("from . import alpha\n", encoding="utf-8")
    (package / "alpha.py").write_text("from app import beta\n", encoding="utf-8")
    (package / "beta.py").write_text("import app.alpha\n", encoding="utf-8")

    graph = build_import_graph(package)

    assert graph["app"] == {"app.alpha"}
    assert graph["app.alpha"] == {"app", "app.beta"}
    assert graph["app.beta"] == {"app.alpha"}
    assert find_import_cycles(graph) == [["app", "app.alpha", "app.beta"]]


def test_parse_error_is_reported_as_value_error(tmp_path: Path):
    package = tmp_path / "app"
    package.mkdir()
    (package / "broken.py").write_text("def unfinished(:\n", encoding="utf-8")

    with pytest.raises(ValueError, match="Cannot parse"):
        build_import_graph(package)


def test_cli_fails_only_when_fail_on_cycle_is_requested(tmp_path: Path, capsys):
    package = tmp_path / "app"
    package.mkdir()
    (package / "a.py").write_text("import app.b\n", encoding="utf-8")
    (package / "b.py").write_text("import app.a\n", encoding="utf-8")

    assert main(["--package-root", str(package)]) == 0
    assert "Detected 1 cyclic component" in capsys.readouterr().out
    assert main(["--package-root", str(package), "--fail-on-cycle"]) == 1
