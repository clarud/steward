from datetime import UTC, datetime
from pathlib import Path

from steward.extraction import MarkdownExtractor, SourceFragmentRepository
from steward.sources import MoveReconciler, SourceInboxContext, SourceInboxContextRepository, SourceRepository
from steward.sources.service import SourceService
from steward.storage import initialize_database


def _setup(tmp_path: Path):
    database = tmp_path / "steward.db"
    initialize_database(database)
    sources = SourceRepository(database)
    scanner = SourceService(sources, SourceFragmentRepository(database), MarkdownExtractor())
    return database, sources, scanner


def test_a_rename_keeps_the_file_identity_and_records_where_it_was(tmp_path: Path) -> None:
    _, sources, scanner = _setup(tmp_path)
    root = tmp_path / "notes"; root.mkdir()
    original = root / "draft.md"; original.write_text("# OpenMP\nloops", encoding="utf-8")
    scanner.scan_source_root(root)
    before = sources.get_by_path(original.resolve())
    renamed = root / "Week 5" / "openmp.md"; renamed.parent.mkdir()
    original.rename(renamed)
    scanner.scan_source_root(root)

    [move] = MoveReconciler(sources).reconcile()

    assert move.source.id == before.id
    assert move.source.path == renamed.resolve()
    assert move.previous_path == original.resolve()
    assert sources.location_history(before.id or 0) == (original.resolve(),)
    assert [item.path for item in sources.list_all()] == [renamed.resolve()]


def test_filing_an_inbox_upload_keeps_its_note(tmp_path: Path) -> None:
    database, sources, scanner = _setup(tmp_path)
    inbox = tmp_path / "inbox"; inbox.mkdir()
    root = tmp_path / "Y4S1"; root.mkdir()
    upload = inbox / "tut05.pdf"; upload.write_bytes(b"tutorial five")
    scanner.scan_source_root(inbox)
    saved = sources.get_by_path(upload.resolve())
    SourceInboxContextRepository(database).set(SourceInboxContext(
        saved.id or 0, None, None, "CS3210 week 5", "telegram", datetime.now(UTC),
    ))
    filed = root / "CS3210" / "tut05.pdf"; filed.parent.mkdir()
    upload.rename(filed)  # what Codex does from INBOX.md
    scanner.scan_source_root(root)

    [move] = MoveReconciler(sources, inbox).reconcile()

    assert move.source.id == saved.id and move.source.path == filed.resolve()
    assert SourceInboxContextRepository(database).get(saved.id or 0).user_context == "CS3210 week 5"


def test_copies_and_ambiguous_duplicates_are_left_as_separate_files(tmp_path: Path) -> None:
    _, sources, scanner = _setup(tmp_path)
    root = tmp_path / "notes"; root.mkdir()
    (root / "a.md").write_text("same bytes", encoding="utf-8")
    (root / "b.md").write_text("same bytes", encoding="utf-8")
    scanner.scan_source_root(root)
    (root / "a.md").rename(root / "c.md")
    (root / "b.md").rename(root / "d.md")
    scanner.scan_source_root(root)

    assert MoveReconciler(sources).reconcile() == ()
    assert len(sources.list_all()) == 4

    kept = root / "kept.md"; kept.write_text("unique", encoding="utf-8")
    scanner.scan_source_root(root)
    (root / "copy.md").write_text("unique", encoding="utf-8")
    scanner.scan_source_root(root)
    assert MoveReconciler(sources).reconcile() == ()
