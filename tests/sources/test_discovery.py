from pathlib import Path

import pytest

from steward.sources import discover_markdown_files


def test_discover_markdown_files_recurses_and_ignores_unsupported_files(
    tmp_path: Path,
) -> None:
    top_level_note = tmp_path / "alpha.md"
    nested_directory = tmp_path / "nested"
    nested_directory.mkdir()
    nested_note = nested_directory / "beta.MD"
    top_level_note.write_text("# Alpha")
    nested_note.write_text("# Beta")
    (tmp_path / "ignore.txt").write_text("Not Markdown")

    discovered = discover_markdown_files(tmp_path)

    assert discovered == [top_level_note.resolve(), nested_note.resolve()]


def test_discover_markdown_files_rejects_missing_root(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="does not exist"):
        discover_markdown_files(tmp_path / "missing")


def test_discover_markdown_files_rejects_file_root(tmp_path: Path) -> None:
    file_path = tmp_path / "note.md"
    file_path.write_text("# Note")

    with pytest.raises(NotADirectoryError, match="not a directory"):
        discover_markdown_files(file_path)

