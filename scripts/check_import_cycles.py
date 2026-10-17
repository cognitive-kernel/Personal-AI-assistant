#!/usr/bin/env python3
"""Report strongly connected components in module-time Python imports.

Imports inside function bodies are deferred until those functions run and are
excluded from the default graph. This avoids reporting intentional lazy-import
edges as if they were eager module-initialization cycles. This diagnostic is
not a proof that imports are safe; pass --fail-on-cycle to gate CI after review.
"""
from __future__ import annotations

import argparse
import ast
from pathlib import Path
import sys
from collections.abc import Iterable


def module_name(path: Path, package_root: Path) -> str:
    """Return the dotted module name for a .py file below package_root."""
    relative = path.relative_to(package_root)
    parts = list(relative.parts)
    if parts[-1] == "__init__.py":
        parts.pop()
    else:
        parts[-1] = Path(parts[-1]).stem
    return ".".join([package_root.name, *parts])


def _import_targets(
    node: ast.Import | ast.ImportFrom,
    current_module: str,
    is_package: bool,
    known_modules: set[str],
) -> set[str]:
    targets: set[str] = set()
    if isinstance(node, ast.Import):
        for alias in node.names:
            if alias.name in known_modules:
                targets.add(alias.name)
        return targets

    parts = current_module.split(".")
    package_parts = parts if is_package else parts[:-1]
    if node.level:
        remove_count = node.level - 1
        if remove_count >= len(package_parts):
            return targets
        base = package_parts[: len(package_parts) - remove_count]
    else:
        base = []

    if node.module:
        base.extend(node.module.split("."))
    base_name = ".".join(base)
    # "from . import child" addresses the package namespace to resolve a
    # child, but does not import the package as a dependency itself.
    if node.module and base_name in known_modules:
        targets.add(base_name)

    # "from app import db" and "from . import db" can import submodules.
    for alias in node.names:
        candidate = ".".join([*base, alias.name]) if base else alias.name
        if candidate in known_modules:
            targets.add(candidate)
    return targets


class _ModuleTimeImports(ast.NodeVisitor):
    """Collect imports executed while defining a module, not inside functions."""

    def __init__(self, current_module: str, is_package: bool, known_modules: set[str]):
        self.current_module = current_module
        self.is_package = is_package
        self.known_modules = known_modules
        self.targets: set[str] = set()

    def visit_Import(self, node: ast.Import) -> None:
        self.targets.update(
            _import_targets(node, self.current_module, self.is_package, self.known_modules)
        )

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        self.targets.update(
            _import_targets(node, self.current_module, self.is_package, self.known_modules)
        )

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        # Decorators, defaults and annotations execute while the function is
        # defined; imports in its body execute only when called.
        for expression in node.decorator_list:
            self.visit(expression)
        for expression in [*node.args.defaults, *node.args.kw_defaults]:
            if expression is not None:
                self.visit(expression)
        annotations = [node.returns]
        annotations.extend(
            argument.annotation
            for argument in (
                *node.args.posonlyargs,
                *node.args.args,
                *node.args.kwonlyargs,
            )
        )
        if node.args.vararg:
            annotations.append(node.args.vararg.annotation)
        if node.args.kwarg:
            annotations.append(node.args.kwarg.annotation)
        for annotation in annotations:
            if annotation is not None:
                self.visit(annotation)

    visit_AsyncFunctionDef = visit_FunctionDef


def build_import_graph(package_root: Path) -> dict[str, set[str]]:
    """Build a local module-time import graph using AST, without importing code."""
    files = sorted(package_root.rglob("*.py"))
    modules = {module_name(path, package_root): path for path in files}
    known_modules = set(modules)
    graph = {name: set() for name in modules}

    for name, path in modules.items():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (OSError, UnicodeError, SyntaxError) as exc:
            raise ValueError(f"Cannot parse {path}: {exc}") from exc
        visitor = _ModuleTimeImports(name, path.name == "__init__.py", known_modules)
        visitor.visit(tree)
        graph[name].update(visitor.targets)
    return graph


def find_import_cycles(graph: dict[str, set[str]]) -> list[list[str]]:
    """Return strongly connected components that contain an import cycle."""
    index = 0
    indices: dict[str, int] = {}
    lowlinks: dict[str, int] = {}
    stack: list[str] = []
    on_stack: set[str] = set()
    cycles: list[list[str]] = []

    def visit(node: str) -> None:
        nonlocal index
        indices[node] = lowlinks[node] = index
        index += 1
        stack.append(node)
        on_stack.add(node)

        for neighbor in sorted(graph.get(node, set())):
            if neighbor not in graph:
                continue
            if neighbor not in indices:
                visit(neighbor)
                lowlinks[node] = min(lowlinks[node], lowlinks[neighbor])
            elif neighbor in on_stack:
                lowlinks[node] = min(lowlinks[node], indices[neighbor])

        if lowlinks[node] == indices[node]:
            component: list[str] = []
            while True:
                member = stack.pop()
                on_stack.remove(member)
                component.append(member)
                if member == node:
                    break
            if len(component) > 1 or node in graph.get(node, set()):
                cycles.append(sorted(component))

    for node in sorted(graph):
        if node not in indices:
            visit(node)
    return sorted(cycles)


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--package-root",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "app",
        help="directory containing the Python package to inspect (default: app/)",
    )
    parser.add_argument(
        "--fail-on-cycle",
        action="store_true",
        help="return exit status 1 when one or more import cycles are found",
    )
    args = parser.parse_args(argv)
    root = args.package_root.resolve()
    if not root.is_dir():
        parser.error(f"package root is not a directory: {root}")

    try:
        graph = build_import_graph(root)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    cycles = find_import_cycles(graph)
    print(f"Scanned {len(graph)} Python modules under {root}.")
    if not cycles:
        print("No import cycles detected by the AST-based checker.")
        return 0
    print(f"Detected {len(cycles)} cyclic component(s):")
    for number, component in enumerate(cycles, start=1):
        print(f"  {number}. " + " -> ".join(component) + " -> (component)")
    print(
        "Note: each row is a strongly connected component, not necessarily "
        "a minimal cycle path."
    )
    return 1 if args.fail_on_cycle else 0


if __name__ == "__main__":
    raise SystemExit(main())
