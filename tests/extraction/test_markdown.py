from datetime import UTC, datetime
from pathlib import Path

import pytest

from steward.extraction import MarkdownExtractor
from steward.sources import Source, SourceType


def make_source(path: Path, *, id: int | None = 1) -> Source:
    timestamp = datetime(2026, 9, 7, 1, 0, tzinfo=UTC)
    return Source(
        id=id,
        path=path,
        content_hash="a" * 64,
        source_type=SourceType.MARKDOWN,
        size_bytes=1,
        modified_at=timestamp,
        first_seen_at=timestamp,
        last_seen_at=timestamp,
    )


def test_extractor_creates_heading_delimited_fragments(tmp_path: Path) -> None:
    source_path = tmp_path / "virtual-memory.md"
    source_path.write_text(
        "# Virtual Memory\n"
        "Overview.\n"
        "\n"
        "## TLB\n"
        "A TLB caches translations.\n"
        "\n"
        "## Page Tables\n"
        "Page tables map addresses.\n",
        encoding="utf-8",
    )

    result = MarkdownExtractor().extract(make_source(source_path))

    assert result.source_id == 1
    assert [fragment.heading for fragment in result.fragments] == [
        "Virtual Memory",
        "TLB",
        "Page Tables",
    ]
    assert result.fragments[1].text == "## TLB\nA TLB caches translations."
    assert result.fragments[1].location == "lines 4-6"


def test_extractor_retains_preamble_as_a_fragment(tmp_path: Path) -> None:
    source_path = tmp_path / "note.md"
    source_path.write_text("Opening note.\n# Topic\nDetails.", encoding="utf-8")

    result = MarkdownExtractor().extract(make_source(source_path))

    assert [fragment.heading for fragment in result.fragments] == [None, "Topic"]
    assert result.fragments[0].text == "Opening note."
    assert result.fragments[0].location == "lines 1-1"


def test_extractor_returns_no_fragments_for_empty_markdown() -> None:
    result = MarkdownExtractor().extract_text(source_id=1, markdown="\n\n")

    assert result.fragments == ()


def test_extractor_rejects_unregistered_source(tmp_path: Path) -> None:
    source_path = tmp_path / "note.md"
    source_path.write_text("# Note", encoding="utf-8")

    with pytest.raises(ValueError, match="persisted Source"):
        MarkdownExtractor().extract(make_source(source_path, id=None))


def test_long_sections_are_split_at_line_breaks_with_exact_line_ranges() -> None:
    from steward.extraction.markdown import MAX_SECTION_CHARACTERS

    line = "x" * 99
    markdown = "# Log\n" + "\n".join([line] * 70) + "\n" + "y" * (MAX_SECTION_CHARACTERS + 10)

    fragments = MarkdownExtractor().extract_text(source_id=1, markdown=markdown).fragments

    assert all(len(fragment.text) <= MAX_SECTION_CHARACTERS for fragment in fragments)
    assert fragments[0].location == "lines 1-30" and fragments[0].heading == "Log"
    assert fragments[-1].location == "lines 72-72"  # an over-long single line keeps its line number
    assert "".join(fragment.text for fragment in fragments).count("x") == 99 * 70
