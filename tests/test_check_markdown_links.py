from pathlib import Path

from scripts.check_markdown_links import find_broken_links


def test_markdown_link_check_detects_missing_local_target_and_reports_line(tmp_path: Path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "README.md").write_text(
        "# Guide\n\n[valid](existing.md)\n[missing](missing.md)\n",
        encoding="utf-8",
    )
    (tmp_path / "docs" / "existing.md").write_text("# Existing\n", encoding="utf-8")

    errors = find_broken_links(tmp_path)

    assert len(errors) == 1
    assert "docs/README.md:4: missing target missing.md" in errors[0]


def test_markdown_link_check_ignores_external_links_and_fenced_examples(tmp_path: Path):
    (tmp_path / "README.md").write_text(
        "External [site](https://example.com/docs) and [mail](mailto:a@example.com).\n"
        "\n```markdown\n[example](does-not-exist.md)\n```\n",
        encoding="utf-8",
    )

    assert find_broken_links(tmp_path) == []


def test_markdown_link_check_rejects_paths_escaping_repository_root(tmp_path: Path):
    (tmp_path / "README.md").write_text("[escape](../outside.md)\n", encoding="utf-8")

    errors = find_broken_links(tmp_path)

    assert len(errors) == 1
    assert "link escapes repository root" in errors[0]


def test_markdown_link_check_accepts_encoded_spaces_and_anchor_only_links(tmp_path: Path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "Reference guide.md").write_text("# Reference\n", encoding="utf-8")
    (tmp_path / "docs" / "README.md").write_text(
        "[guide](Reference%20guide.md#reference) and [section](#local-section)\n",
        encoding="utf-8",
    )

    assert find_broken_links(tmp_path) == []
