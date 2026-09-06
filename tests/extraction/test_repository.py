from datetime import UTC, datetime
from pathlib import Path

import pytest

from steward.extraction import (
    ExtractionResult,
    MarkdownExtractor,
    SourceFragment,
    SourceFragmentRepository,
    UnknownSourceError,
)
from steward.sources import Source, SourceRepository, SourceType
from steward.storage import initialize_database


def register_source(database_path: Path, source_path: Path) -> Source:
    timestamp = datetime(2026, 9, 7, 1, 0, tzinfo=UTC)
    return SourceRepository(database_path).add(
        Source(
            id=None,
            path=source_path,
            content_hash="a" * 64,
            source_type=SourceType.MARKDOWN,
            size_bytes=source_path.stat().st_size,
            modified_at=timestamp,
            first_seen_at=timestamp,
            last_seen_at=timestamp,
        )
    )


def test_fragment_repository_stores_and_reads_fragments(tmp_path: Path) -> None:
    database_path = tmp_path / "steward.db"
    initialize_database(database_path)
    source_path = tmp_path / "note.md"
    source_path.write_text("# TLB\nA TLB caches translations.", encoding="utf-8")
    source = register_source(database_path, source_path)
    result = MarkdownExtractor().extract(source)
    repository = SourceFragmentRepository(database_path)

    stored_fragments = repository.replace_for_source(result)

    assert all(fragment.id is not None for fragment in stored_fragments)
    assert repository.list_for_source(source.id or 0) == stored_fragments
    assert stored_fragments[0].source_id == source.id
    assert stored_fragments[0].ordinal == 0
    assert stored_fragments[0].location == "lines 1-2"


def test_fragment_repository_replaces_stale_derived_fragments(tmp_path: Path) -> None:
    database_path = tmp_path / "steward.db"
    initialize_database(database_path)
    source_path = tmp_path / "note.md"
    source_path.write_text("# First\nOld text.", encoding="utf-8")
    source = register_source(database_path, source_path)
    repository = SourceFragmentRepository(database_path)
    repository.replace_for_source(MarkdownExtractor().extract(source))
    source_path.write_text("# Second\nNew text.", encoding="utf-8")

    stored_fragments = repository.replace_for_source(MarkdownExtractor().extract(source))

    assert [fragment.heading for fragment in stored_fragments] == ["Second"]
    assert [fragment.text for fragment in repository.list_for_source(source.id or 0)] == [
        "# Second\nNew text."
    ]


def test_fragment_repository_rejects_unknown_source(tmp_path: Path) -> None:
    database_path = tmp_path / "steward.db"
    initialize_database(database_path)
    result = ExtractionResult(
        source_id=999,
        fragments=(
            SourceFragment(
                id=None,
                source_id=999,
                heading="Missing",
                ordinal=0,
                text="# Missing",
                location="lines 1-1",
            ),
        ),
    )

    with pytest.raises(UnknownSourceError, match="999"):
        SourceFragmentRepository(database_path).replace_for_source(result)
