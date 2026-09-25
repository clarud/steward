from datetime import UTC, datetime
from pathlib import Path

from steward.activity import ActivityService
from steward.events import IncomingEvent
from steward.capture import InboxCaptureService
from steward.intake import ProvisionalIntakeRepository, ProvisionalIntakeService
from steward.roots import SourceRootRepository
from steward.sources import InboxQueue, SourceInboxContext, SourceInboxContextRepository, SourceRepository
from steward.storage import initialize_database


def _event(message_id: str, text: str | None = None) -> IncomingEvent:
    return IncomingEvent(
        f"telegram:{message_id}", "telegram", "100", message_id, None,
        datetime(2026, 9, 24, tzinfo=UTC), text,
    )


def _setup(tmp_path: Path):
    database = tmp_path / "steward.db"
    initialize_database(database)
    inbox = tmp_path / "vault" / "inbox"
    sources = SourceRepository(database)
    queue = InboxQueue(
        sources, inbox,
        roots=SourceRootRepository(database),
        inbox_contexts=SourceInboxContextRepository(database),
    )
    capture = InboxCaptureService(inbox, sources, activity_service=ActivityService(database), inbox_queue=queue)
    return database, inbox, sources, queue, capture


def test_capture_lists_the_new_file_in_inbox_md(tmp_path: Path) -> None:
    _, inbox, _, queue, capture = _setup(tmp_path)

    result = capture.capture_text(_event("7", "compare OpenMP schedules"))

    listing = (inbox / "INBOX.md").read_text(encoding="utf-8")
    assert "1 file waiting." in listing
    assert f"## 1. {result.source.path.name}" in listing
    assert "via Telegram" in listing
    assert "Intended root: not given" in listing
    assert queue.path == inbox / "INBOX.md"


def test_intended_root_note_and_guidance_reach_the_queue(tmp_path: Path) -> None:
    database, _, _, queue, capture = _setup(tmp_path)
    course = tmp_path / "Y4S1"; course.mkdir()
    (course / "AGENTS.md").write_text("File tutorials under Tutorials/.", encoding="utf-8")
    root = SourceRootRepository(database).add("Y4S1", course)
    result = capture.capture_text(_event("8", "tutorial 5 answers with the private worked solution kept inside"))
    SourceInboxContextRepository(database).set(SourceInboxContext(
        result.source.id or 0, root.id, root.name, "CS3210 week 5", "telegram", datetime.now(UTC),
    ))

    queue.refresh()

    listing = queue.path.read_text(encoding="utf-8")
    assert f"- Intended root: Y4S1 (`{course.resolve()}`)" in listing
    assert "- Note: CS3210 week 5" in listing
    assert f"- Guidance: `{course.resolve() / 'AGENTS.md'}`" in listing
    assert "kept inside" not in listing  # file contents never appear, only the file name


def test_filed_files_drop_off_and_unchanged_lists_are_not_rewritten(tmp_path: Path) -> None:
    _, inbox, _, queue, capture = _setup(tmp_path)
    first = capture.capture_text(_event("9", "first note"))
    capture.capture_text(_event("10", "second note"))
    first.source.path.rename(tmp_path / first.source.path.name)

    queue.refresh()
    written = queue.path.stat().st_mtime_ns
    queue.refresh()

    listing = queue.path.read_text(encoding="utf-8")
    assert "1 file waiting." in listing
    assert first.source.path.name not in listing
    assert queue.path.stat().st_mtime_ns == written
    assert not (inbox / "INBOX.md.tmp").exists()


def test_empty_inbox_says_nothing_is_waiting(tmp_path: Path) -> None:
    _, _, _, queue, _ = _setup(tmp_path)

    path = queue.refresh()

    assert "Nothing is waiting." in path.read_text(encoding="utf-8")


def test_accepting_a_staged_upload_records_its_intended_root_in_the_queue(tmp_path: Path) -> None:
    database, _, _, queue, capture = _setup(tmp_path)
    course = tmp_path / "Y4S1"; course.mkdir()
    root = SourceRootRepository(database).add("Y4S1", course)
    intakes = ProvisionalIntakeRepository(database)
    intake = ProvisionalIntakeService(
        tmp_path / "cache", intakes, capture,
        ActivityService(database),
        roots=SourceRootRepository(database), inbox_contexts=SourceInboxContextRepository(database),
    )
    staged = intake.stage_text(_event("11", "note: tutorial 5 answers for CS3210"))
    intakes.set_intended_root(staged.id or 0, root.id)

    intake.accept(staged.id or 0, _event("13"))

    assert "- Intended root: Y4S1" in queue.path.read_text(encoding="utf-8")
