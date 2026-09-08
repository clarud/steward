from pathlib import Path

import pytest

from steward.sources import SourceType, discover_markdown_files, discover_source_files, source_type_for_path


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


def test_discover_source_files_includes_every_currently_supported_type(tmp_path: Path) -> None:
    markdown = tmp_path / "alpha.md"; markdown.write_text("# Alpha")
    text = tmp_path / "beta.txt"; text.write_text("Beta")
    pdf = tmp_path / "nested" / "gamma.PDF"; pdf.parent.mkdir(); pdf.write_bytes(b"%PDF")
    image = tmp_path / "receipt.png"; image.write_bytes(b"image")

    assert discover_source_files(tmp_path) == [
        markdown.resolve(), text.resolve(), pdf.resolve(), image.resolve()
    ]
    assert source_type_for_path(pdf) is SourceType.PDF
    assert source_type_for_path(image) is SourceType.IMAGE
