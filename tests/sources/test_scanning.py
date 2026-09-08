from datetime import UTC, datetime, timedelta
from pathlib import Path

from steward.sources import ScanResult, SourceRepository, SourceStatus, SourceType, scan_markdown_root, scan_source_root
from steward.storage import initialize_database


def make_repository(tmp_path: Path) -> SourceRepository:
    database_path = tmp_path / ".steward" / "steward.db"
    initialize_database(database_path)
    return SourceRepository(database_path)


def test_scan_registers_new_markdown_and_ignores_unsupported_files(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    note_path = vault / "note.md"
    note_path.write_text("# Note")
    (vault / "ignore.txt").write_text("Ignore me")
    repository = make_repository(tmp_path)
    scanned_at = datetime(2026, 9, 6, 1, 0, tzinfo=UTC)

    result = scan_markdown_root(vault, repository, scanned_at=scanned_at)

    assert result == ScanResult(new=1, updated=0, unchanged=0, missing=0)
    source = repository.get_by_path(note_path.resolve())
    assert source is not None
    assert source.first_seen_at == scanned_at
    assert source.status is SourceStatus.ACTIVE


def test_rescan_of_unchanged_file_is_idempotent(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    note_path = vault / "note.md"
    note_path.write_text("# Note")
    repository = make_repository(tmp_path)
    first_scan = datetime(2026, 9, 6, 1, 0, tzinfo=UTC)
    second_scan = first_scan + timedelta(minutes=1)
    scan_markdown_root(vault, repository, scanned_at=first_scan)

    result = scan_markdown_root(vault, repository, scanned_at=second_scan)

    assert result == ScanResult(new=0, updated=0, unchanged=1, missing=0)
    source = repository.get_by_path(note_path.resolve())
    assert source is not None
    assert source.last_seen_at == second_scan


def test_rescan_updates_a_modified_file(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    note_path = vault / "note.md"
    note_path.write_text("# First")
    repository = make_repository(tmp_path)
    first_scan = datetime(2026, 9, 6, 1, 0, tzinfo=UTC)
    scan_markdown_root(vault, repository, scanned_at=first_scan)
    first_source = repository.get_by_path(note_path.resolve())
    assert first_source is not None
    note_path.write_text("# Second")

    result = scan_markdown_root(
        vault, repository, scanned_at=first_scan + timedelta(minutes=1)
    )

    updated_source = repository.get_by_path(note_path.resolve())
    assert result.updated == 1
    assert updated_source is not None
    assert updated_source.content_hash != first_source.content_hash


def test_rescan_marks_disappeared_file_missing(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    note_path = vault / "note.md"
    note_path.write_text("# Note")
    repository = make_repository(tmp_path)
    scanned_at = datetime(2026, 9, 6, 1, 0, tzinfo=UTC)
    scan_markdown_root(vault, repository, scanned_at=scanned_at)
    note_path.unlink()

    result = scan_markdown_root(
        vault, repository, scanned_at=scanned_at + timedelta(minutes=1)
    )

    assert result.missing == 1
    source = repository.get_by_path(note_path.resolve())
    assert source is not None
    assert source.status is SourceStatus.MISSING


def test_scan_preserves_duplicate_content_at_two_paths(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    (vault / "one").mkdir(parents=True)
    (vault / "two").mkdir()
    (vault / "one" / "note.md").write_text("# Same")
    (vault / "two" / "note.md").write_text("# Same")
    repository = make_repository(tmp_path)

    result = scan_markdown_root(
        vault, repository, scanned_at=datetime(2026, 9, 6, 1, 0, tzinfo=UTC)
    )

    assert result.new == 2


def test_general_scan_registers_markdown_and_plain_text(tmp_path: Path) -> None:
    vault = tmp_path / "vault"; vault.mkdir()
    markdown = vault / "note.md"; markdown.write_text("# Note")
    text = vault / "todo.txt"; text.write_text("Study")
    repository = make_repository(tmp_path)

    result = scan_source_root(vault, repository, scanned_at=datetime(2026, 9, 6, 1, 0, tzinfo=UTC))

    assert result == ScanResult(new=2, updated=0, unchanged=0, missing=0)
    assert repository.get_by_path(markdown.resolve()).source_type is SourceType.MARKDOWN
    assert repository.get_by_path(text.resolve()).source_type is SourceType.PLAIN_TEXT


def test_general_scan_registers_docx_files(tmp_path: Path) -> None:
    vault = tmp_path / "vault"; vault.mkdir()
    document = vault / "notes.docx"; document.write_bytes(b"PK\x03\x04")
    repository = make_repository(tmp_path)

    result = scan_source_root(vault, repository, scanned_at=datetime(2026, 9, 6, 1, 0, tzinfo=UTC))

    assert result.new == 1
    assert repository.get_by_path(document.resolve()).source_type is SourceType.DOCX
