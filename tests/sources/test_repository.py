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
