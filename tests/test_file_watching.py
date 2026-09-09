from pathlib import Path

from steward.extraction import MarkdownExtractor, SourceFragmentRepository
from steward.file_watching import FileWatchService
from steward.sources import SourceRepository
from steward.sources.service import SourceService
from steward.storage import initialize_database


def test_watcher_debounces_events_and_hashes_before_reextracting(tmp_path: Path) -> None:
    database = tmp_path / "steward.db"; initialize_database(database)
    root = tmp_path / "vault"; root.mkdir()
    note = root / "note.md"; note.write_text("# First\nOne", encoding="utf-8")
    repository = SourceRepository(database)
    source_service = SourceService(repository, SourceFragmentRepository(database), MarkdownExtractor())
    watcher = FileWatchService(root, source_service, debounce_seconds=1.0)

    watcher.notify(note, observed_at=10.0)
    assert watcher.flush(now=10.5) == {}
    assert watcher.flush(now=11.0) == {note.resolve(): "new"}
    source = repository.get_by_path(note.resolve())
    assert source is not None

    watcher.notify(note, observed_at=12.0)
    assert watcher.flush(now=13.0) == {note.resolve(): "unchanged"}
    note.write_text("# Second\nTwo", encoding="utf-8")
    watcher.notify(note, observed_at=14.0)
    assert watcher.flush(now=15.0) == {note.resolve(): "updated"}
    assert SourceFragmentRepository(database).list_for_source(source.id or 0)[0].heading == "Second"


def test_watcher_marks_deleted_registered_source_missing(tmp_path: Path) -> None:
    database = tmp_path / "steward.db"; initialize_database(database)
    root = tmp_path / "vault"; root.mkdir(); note = root / "note.md"; note.write_text("text")
    repository = SourceRepository(database)
    source_service = SourceService(repository, SourceFragmentRepository(database), MarkdownExtractor())
    watcher = FileWatchService(root, source_service)
    watcher.notify(note, observed_at=0.0); watcher.flush(now=1.0)
    note.unlink()
    watcher.notify(note, observed_at=2.0)
    assert watcher.flush(now=3.0) == {note.resolve(): "missing"}


def test_watcher_ignores_operational_and_configured_exclusions(tmp_path: Path) -> None:
    database = tmp_path / "steward.db"; initialize_database(database)
    root = tmp_path / "vault"; root.mkdir()
    generated = root / "generated"; generated.mkdir()
    operational = root / ".steward"; operational.mkdir()
    generated_note = generated / "output.md"; generated_note.write_text("# Output", encoding="utf-8")
    internal_note = operational / "internal.md"; internal_note.write_text("# Internal", encoding="utf-8")
    repository = SourceRepository(database)
    watcher = FileWatchService(root, SourceService(repository, SourceFragmentRepository(database), MarkdownExtractor()), exclusions=(Path("generated"),))

    watcher.notify(generated_note, observed_at=0.0)
    watcher.notify(internal_note, observed_at=0.0)

    assert watcher.flush(now=1.0) == {}
    assert repository.list_all() == []
