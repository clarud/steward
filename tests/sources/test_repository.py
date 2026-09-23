from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from steward.sources import (
    Source,
    SourceAlreadyExistsError,
    SourceNotFoundError,
    SourceRepository,
    SourceStatus,
    SourceType,
)
from steward.extraction import ExtractionResult, SourceFragment, SourceFragmentRepository
from steward.storage import initialize_database


def make_unregistered_source(path: Path, content_hash: str = "a" * 64) -> Source:
    scanned_at = datetime(2026, 9, 3, 1, 0, tzinfo=UTC)
    return Source(
        id=None,
        path=path,
        content_hash=content_hash,
        source_type=SourceType.MARKDOWN,
        size_bytes=128,
        modified_at=scanned_at,
        first_seen_at=scanned_at,
        last_seen_at=scanned_at,
    )


def test_repository_stores_and_reads_a_source(tmp_path: Path) -> None:
    database_path = tmp_path / "steward.db"
    initialize_database(database_path)
    repository = SourceRepository(database_path)

    stored = repository.add(make_unregistered_source(Path("vault/note.md")))

    assert stored.id is not None
    assert repository.get_by_path(Path("vault/note.md")) == stored


def test_repository_preserves_duplicate_content_at_different_paths(tmp_path: Path) -> None:
    database_path = tmp_path / "steward.db"
    initialize_database(database_path)
    repository = SourceRepository(database_path)

    first = repository.add(make_unregistered_source(Path("vault/course/note.md")))
    second = repository.add(make_unregistered_source(Path("vault/project/note.md")))

    assert first.id != second.id
    assert first.content_hash == second.content_hash


def test_repository_finds_active_sources_by_filename_with_type_and_path_scope(tmp_path: Path) -> None:
    database = tmp_path / "steward.db"; initialize_database(database)
    course = tmp_path / "course"; course.mkdir()
    other = tmp_path / "other"; other.mkdir()
    repository = SourceRepository(database)
    expected = repository.add(replace(
        make_unregistered_source(course / "network-architecture.pdf"), source_type=SourceType.PDF,
    ))
    repository.add(make_unregistered_source(other / "network-architecture.md"))

    matches = repository.search_filenames(
        "network architecture", source_types={SourceType.PDF}, path_prefix=course,
    )

    assert matches == (expected,)


def test_repository_rejects_a_second_source_at_the_same_path(tmp_path: Path) -> None:
    database_path = tmp_path / "steward.db"
    initialize_database(database_path)
    repository = SourceRepository(database_path)
    source = make_unregistered_source(Path("vault/note.md"))
    repository.add(source)

    with pytest.raises(SourceAlreadyExistsError, match="already registered"):
        repository.add(source)


def test_repository_returns_none_for_an_unknown_path(tmp_path: Path) -> None:
    database_path = tmp_path / "steward.db"
    initialize_database(database_path)

    assert SourceRepository(database_path).get_by_path(Path("vault/missing.md")) is None


def test_repository_lists_active_and_missing_sources(tmp_path: Path) -> None:
    database_path = tmp_path / "steward.db"
    initialize_database(database_path)
    repository = SourceRepository(database_path)
    active_source = repository.add(make_unregistered_source(Path("vault/active.md")))
    missing_source = repository.add(make_unregistered_source(Path("vault/missing.md")))
    repository.update(
        Source(
            id=missing_source.id,
            path=missing_source.path,
            content_hash=missing_source.content_hash,
            source_type=missing_source.source_type,
            size_bytes=missing_source.size_bytes,
            modified_at=missing_source.modified_at,
            first_seen_at=missing_source.first_seen_at,
            last_seen_at=missing_source.last_seen_at,
            status=SourceStatus.MISSING,
        )
    )

    assert repository.list_active() == [active_source]
    assert repository.list_all() == [active_source, repository.get_by_path(missing_source.path)]


def test_repository_rejects_updating_an_unregistered_source(tmp_path: Path) -> None:
    database_path = tmp_path / "steward.db"
    initialize_database(database_path)

    with pytest.raises(ValueError, match="persisted Source"):
        SourceRepository(database_path).update(
            make_unregistered_source(Path("vault/note.md"))
        )


def test_repository_rejects_updating_an_unknown_source(tmp_path: Path) -> None:
    database_path = tmp_path / "steward.db"
    initialize_database(database_path)
    source = make_unregistered_source(Path("vault/note.md"))
    unknown_source = Source(
        id=999,
        path=source.path,
        content_hash=source.content_hash,
        source_type=source.source_type,
        size_bytes=source.size_bytes,
        modified_at=source.modified_at,
        first_seen_at=source.first_seen_at,
        last_seen_at=source.last_seen_at,
    )

    with pytest.raises(SourceNotFoundError, match="999"):
        SourceRepository(database_path).update(unknown_source)


def test_unregister_removes_registry_and_derived_data_but_retains_original_file(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "steward.db"
    initialize_database(database_path)
    source_path = tmp_path / "vault" / "note.md"
    source_path.parent.mkdir()
    source_path.write_text("# Retained original", encoding="utf-8")
    repository = SourceRepository(database_path)
    source = repository.add(make_unregistered_source(source_path))
    fragments = SourceFragmentRepository(database_path)
    fragments.replace_for_source(
        ExtractionResult(
            source_id=source.id or 0,
            fragments=(
                SourceFragment(
                    id=None,
                    source_id=source.id or 0,
                    heading="Retained original",
                    ordinal=0,
                    text="The derived index must go away.",
                    location="lines 1-1",
                ),
            ),
        )
    )

    removed = repository.unregister(source.id or 0)

    assert removed == source
    assert source_path.read_text(encoding="utf-8") == "# Retained original"
    assert repository.get_by_id(source.id or 0) is None
    assert fragments.list_for_source(source.id or 0) == ()
    assert fragments.search("derived") == ()
