from datetime import UTC, datetime
from pathlib import Path

from steward.extraction import MarkdownExtractor, SourceFragmentRepository
from steward.retrieval import LexicalSearchService
from steward.sources import Source, SourceRepository, SourceType
from steward.storage import initialize_database


def test_lexical_search_returns_source_and_matching_fragment(tmp_path: Path) -> None:
    database_path = tmp_path / "steward.db"
    initialize_database(database_path)
    source_path = tmp_path / "virtual-memory.md"
    source_path.write_text("# TLB\nA TLB caches address translations.", encoding="utf-8")
    timestamp = datetime(2026, 9, 7, 1, 0, tzinfo=UTC)
    source = SourceRepository(database_path).add(
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
    fragment_repository = SourceFragmentRepository(database_path)
    fragment_repository.replace_for_source(MarkdownExtractor().extract(source))

    hits = LexicalSearchService(SourceRepository(database_path), fragment_repository).search(
        "address translations"
    )

    assert len(hits) == 1
    assert hits[0].source == source
    assert hits[0].fragment.heading == "TLB"
