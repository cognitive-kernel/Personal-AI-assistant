#!/usr/bin/env python3
"""Validate local Markdown link targets without making network requests.

External URLs are deliberately not fetched: this check is deterministic, safe
for CI, and only verifies repository-relative documentation links.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from urllib.parse import unquote, urlsplit

_LINK = re.compile(r"(?<!!)\[[^\]]*\]\((<[^>]+>|[^)\s]+)(?:\s+[^)]*)?\)")
_FENCE = re.compile(r"^\s{0,3}(`{3,}|~{3,})")


def _without_fenced_code(text: str) -> str:
    """Remove fenced code blocks so examples are not interpreted as links."""
    kept: list[str] = []
    fence_char = ""
    fence_size = 0
    for line in text.splitlines():
        match = _FENCE.match(line)
        if match:
            marker = match.group(1)
            if not fence_char:
                fence_char, fence_size = marker[0], len(marker)
                kept.append("")
                continue
            if marker[0] == fence_char and len(marker) >= fence_size:
                fence_char, fence_size = "", 0
                kept.append("")
                continue
        kept.append("" if fence_char else line)
    return "\n".join(kept)


def find_broken_links(root: Path) -> list[str]:
    root = root.resolve()
    errors: list[str] = []
    excluded = {".git", ".venv", "venv", "node_modules", "__pycache__", ".pytest_cache"}
    markdown_files = sorted(
        path for path in root.rglob("*.md")
        if not any(part in excluded for part in path.relative_to(root).parts)
    )
    for source in markdown_files:
        source_name = source.relative_to(root).as_posix()
        try:
            content = _without_fenced_code(source.read_text(encoding="utf-8"))
        except (OSError, UnicodeError) as exc:
            errors.append(f"{source_name}: cannot read Markdown: {exc}")
            continue
        for match in _LINK.finditer(content):
            destination = match.group(1)
            if destination.startswith("<") and destination.endswith(">"):
                destination = destination[1:-1]
            parsed = urlsplit(destination)
            if parsed.scheme or parsed.netloc or not parsed.path:
                continue
            target = (source.parent / unquote(parsed.path)).resolve()
            try:
                target.relative_to(root)
            except ValueError:
                errors.append(
                    f"{source_name}: link escapes repository root: {destination}"
                )
                continue
            if not target.exists():
                line = content.count("\n", 0, match.start()) + 1
                errors.append(
                    f"{source_name}:{line}: missing target {destination}"
                )
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args(argv)
    errors = find_broken_links(args.root)
    if errors:
        print("\n".join(errors), file=sys.stderr)
        print(f"Markdown link check failed: {len(errors)} broken link(s).", file=sys.stderr)
        return 1
    print(f"Markdown link check passed for {len(list(args.root.rglob('*.md')))} Markdown files.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
