from datetime import UTC, datetime
from dataclasses import replace
from pathlib import Path

from steward.extraction import ExtractionResult, MarkdownExtractor, SourceFragment, SourceFragmentRepository
from steward.retrieval import LexicalSearchService
from steward.sources import Source, SourceRepository, SourceStatus, SourceType
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
    assert hits[0].highlighted_text == "# TLB\nA TLB caches [address] [translations]."


def test_lexical_search_excludes_fragments_of_missing_originals(tmp_path: Path) -> None:
    database_path = tmp_path / "steward.db"
    initialize_database(database_path)
    source_path = tmp_path / "missing.md"
    source_path.write_text("# TLB\nA TLB caches address translations.", encoding="utf-8")
    timestamp = datetime(2026, 9, 7, 1, 0, tzinfo=UTC)
    sources = SourceRepository(database_path)
    source = sources.add(
        Source(None, source_path, "a" * 64, SourceType.MARKDOWN, source_path.stat().st_size,
               timestamp, timestamp, timestamp)
    )
    fragments = SourceFragmentRepository(database_path)
    fragments.replace_for_source(MarkdownExtractor().extract(source))
    sources.update(replace(source, status=SourceStatus.MISSING))

    hits = LexicalSearchService(sources, fragments).search("address translations")

    assert hits == ()


def test_lexical_search_can_filter_by_source_type(tmp_path: Path) -> None:
    database_path = tmp_path / "steward.db"
    initialize_database(database_path)
    timestamp = datetime(2026, 9, 7, 1, 0, tzinfo=UTC)
    sources = SourceRepository(database_path)
    markdown_path = tmp_path / "note.md"; markdown_path.write_text("# TLB\nAddress translations", encoding="utf-8")
    text_path = tmp_path / "note.txt"; text_path.write_text("Address translations", encoding="utf-8")
    markdown = sources.add(Source(None, markdown_path, "a" * 64, SourceType.MARKDOWN, markdown_path.stat().st_size,
                                  timestamp, timestamp, timestamp))
    plain_text = sources.add(Source(None, text_path, "b" * 64, SourceType.PLAIN_TEXT, text_path.stat().st_size,
                                    timestamp, timestamp, timestamp))
    fragments = SourceFragmentRepository(database_path)
    fragments.replace_for_source(ExtractionResult(markdown.id or 0, (
        SourceFragment(None, markdown.id or 0, "TLB", 0, "Address translations", "lines 1-2"),
    )))
    fragments.replace_for_source(ExtractionResult(plain_text.id or 0, (
        SourceFragment(None, plain_text.id or 0, None, 0, "Address translations", "entire file"),
    )))

    hits = LexicalSearchService(sources, fragments).search(
        "translations", source_types={SourceType.PLAIN_TEXT}
    )

    assert [hit.source.id for hit in hits] == [plain_text.id]


def test_lexical_search_can_filter_to_a_path_subtree(tmp_path: Path) -> None:
    database_path = tmp_path / "steward.db"; initialize_database(database_path)
    timestamp = datetime(2026, 9, 7, 1, 0, tzinfo=UTC)
    sources = SourceRepository(database_path)
    course = tmp_path / "vault" / "courses" / "os" / "tlb.md"; course.parent.mkdir(parents=True); course.write_text("TLB")
    other = tmp_path / "vault" / "projects" / "tlb.md"; other.parent.mkdir(parents=True); other.write_text("TLB")
    course_source = sources.add(Source(None, course, "a" * 64, SourceType.MARKDOWN, 3, timestamp, timestamp, timestamp))
    other_source = sources.add(Source(None, other, "b" * 64, SourceType.MARKDOWN, 3, timestamp, timestamp, timestamp))
    fragments = SourceFragmentRepository(database_path)
    for source in (course_source, other_source):
        fragments.replace_for_source(ExtractionResult(source.id or 0, (SourceFragment(None, source.id or 0, None, 0, "TLB cache", "line 1"),)))

    hits = LexicalSearchService(sources, fragments).search("TLB", path_prefix=course.parent)

    assert [hit.source.id for hit in hits] == [course_source.id]
