from datetime import UTC, datetime
from pathlib import Path

import pytest

from steward.activity import ActivityService, ActivityType
from steward.capture import InboxCaptureService
from steward.events import IncomingEvent
from steward.extraction import SourceFragmentRepository
from steward.intake import ProvisionalIntakeRepository, ProvisionalIntakeService
from steward.roots import SourceRootRepository
from steward.sources import SourceRepository
from steward.sources.inbox_context import SourceInboxContextRepository
from steward.storage import initialize_database


def make_event(
    *, event_id: str = "telegram:42", chat_id: str = "100", attachment: str = "OpenMP notes.pdf"
) -> IncomingEvent:
    return IncomingEvent(
        event_id, "telegram", chat_id, "7", None, datetime(2026, 9, 9, tzinfo=UTC),
        None, (attachment,),
    )


def make_service(tmp_path: Path) -> tuple[ProvisionalIntakeService, SourceRepository, ActivityService]:
    database_path = tmp_path / "steward.db"
    initialize_database(database_path)
    sources = SourceRepository(database_path)
    activity = ActivityService(database_path)
    capture = InboxCaptureService(
        tmp_path / "vault" / "inbox", sources, SourceFragmentRepository(database_path), activity
    )
    service = ProvisionalIntakeService(
        tmp_path / ".steward" / "cache" / "intake", ProvisionalIntakeRepository(database_path), capture, activity,
    )
    return service, sources, activity


def test_file_is_staged_without_registering_a_source_until_saved(tmp_path: Path) -> None:
    service, sources, activity = make_service(tmp_path)
    original = tmp_path / "download.pdf"
    original.write_bytes(b"pdf bytes")
    event = make_event()

    intake = service.stage_file(event, original)

    assert intake.status == "pending"
    assert intake.staged_path.is_file()
    assert sources.list_all() == []
    assert activity.list_recent()[0].event_type is ActivityType.INTAKE_PROPOSED

    saved = service.accept(intake.id or 0, event)

    assert saved.duplicate is False
    assert saved.source.path.is_file()
    assert not intake.staged_path.exists()
    assert sources.list_all() == [saved.source]
    assert activity.list_recent()[0].event_type is ActivityType.INTAKE_ACCEPTED


def test_intended_root_and_note_are_saved_as_inbox_metadata_without_moving_the_file(tmp_path: Path) -> None:
    database = tmp_path / "steward.db"; initialize_database(database)
    root_path = tmp_path / "Y4S1"; root_path.mkdir()
    roots = SourceRootRepository(database)
    root = roots.add("Y4S1", root_path)
    sources = SourceRepository(database)
    activity = ActivityService(database)
    capture = InboxCaptureService(tmp_path / "vault" / "inbox", sources, activity_service=activity)
    contexts = SourceInboxContextRepository(database)
    service = ProvisionalIntakeService(
        tmp_path / ".steward" / "cache" / "intake", ProvisionalIntakeRepository(database), capture, activity,
        roots=roots, inbox_contexts=contexts,
    )
    original = tmp_path / "lecture.pdf"; original.write_bytes(b"course material")
    intake = service.stage_file(make_event(attachment=original.name), original)

    selected = service.set_intended_root(intake.id or 0, "100", root.id)
    service.add_note(intake.id or 0, "100", "CS3210   week 5")
    saved = service.accept(intake.id or 0, make_event(attachment=original.name))
    context = contexts.get(saved.source.id or 0)

    assert selected.intended_root_id == root.id
    assert context is not None and context.intended_root_name == "Y4S1"
    assert context.user_context == "CS3210 week 5"
    assert saved.source.path.is_relative_to(tmp_path / "vault" / "inbox")
    assert not saved.source.path.is_relative_to(root_path)


def test_discard_removes_staged_original_without_creating_a_source(tmp_path: Path) -> None:
    service, sources, activity = make_service(tmp_path)
    original = tmp_path / "download.pdf"
    original.write_bytes(b"pdf bytes")
    intake = service.stage_file(make_event(attachment=original.name), original)

    discarded = service.discard(intake.id or 0, "100")

    assert discarded.status == "discarded"
    assert not intake.staged_path.exists()
    assert sources.list_all() == []
    assert activity.list_recent()[0].event_type is ActivityType.INTAKE_DISCARDED


def test_another_chat_cannot_save_or_annotate_a_staged_file(tmp_path: Path) -> None:
    service, _, _ = make_service(tmp_path)
    original = tmp_path / "download.pdf"
    original.write_bytes(b"pdf bytes")
    intake = service.stage_file(make_event(), original)

    with pytest.raises(ValueError, match="another chat"):
        service.accept(intake.id or 0, make_event(event_id="telegram:43", chat_id="other"))
    with pytest.raises(ValueError, match="another chat"):
        service.add_note(intake.id or 0, "other", "hijack")


def test_text_note_is_staged_until_saved(tmp_path: Path) -> None:
    service, sources, _ = make_service(tmp_path)
    event = IncomingEvent(
        "telegram:text", "telegram", "100", "8", None, datetime(2026, 9, 9, tzinfo=UTC),
        "A note about OpenMP scheduling.",
    )

    intake = service.stage_text(event)
    assert intake.staged_path.read_text(encoding="utf-8") == "A note about OpenMP scheduling."
    saved = service.accept(intake.id or 0, make_event(event_id="telegram:accept"))

    assert saved.source.path.read_text(encoding="utf-8") == "A note about OpenMP scheduling."
    assert not intake.staged_path.exists()
    assert sources.get_by_id(saved.source.id or 0) == saved.source


def test_duplicate_delivery_reuses_one_pending_intake(tmp_path: Path) -> None:
    service, sources, activity = make_service(tmp_path)
    original = tmp_path / "download.pdf"
    original.write_bytes(b"pdf bytes")
    event = make_event()

    first = service.stage_file(event, original)
    second = service.stage_file(event, original)

    assert second == first
    assert sources.list_all() == []
    assert [item.event_type for item in activity.list_recent()] == [ActivityType.INTAKE_PROPOSED]


def test_pending_intake_can_be_saved_after_a_restart(tmp_path: Path) -> None:
    service, sources, activity = make_service(tmp_path)
    original = tmp_path / "download.pdf"
    original.write_bytes(b"pdf bytes")
    intake = service.stage_file(make_event(), original)
    database_path = tmp_path / "steward.db"
    restarted = ProvisionalIntakeService(
        tmp_path / ".steward" / "cache" / "intake",
        ProvisionalIntakeRepository(database_path),
        InboxCaptureService(tmp_path / "vault" / "inbox", sources, SourceFragmentRepository(database_path), activity),
        activity,
    )

    saved = restarted.accept(intake.id or 0, make_event(event_id="telegram:accept"))

    assert saved.source.path.is_file()
    assert not intake.staged_path.exists()
