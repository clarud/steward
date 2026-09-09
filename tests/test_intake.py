from datetime import UTC, datetime
from pathlib import Path

import pytest

from steward.activity import ActivityService, ActivityType
from steward.capture import InboxCaptureService
from steward.events import IncomingEvent
from steward.extraction import SourceFragmentRepository
from steward.intake import ProvisionalIntakeRepository, ProvisionalIntakeService
from steward.sources import SourceRepository
from steward.storage import initialize_database


def make_event(*, event_id: str = "telegram:42", chat_id: str = "100") -> IncomingEvent:
    return IncomingEvent(
        event_id, "telegram", chat_id, "7", None, datetime(2026, 9, 9, tzinfo=UTC),
        None, ("OpenMP notes.pdf",),
    )


def make_service(tmp_path: Path) -> tuple[ProvisionalIntakeService, SourceRepository, ActivityService]:
    database_path = tmp_path / "steward.db"
    initialize_database(database_path)
    sources = SourceRepository(database_path)
    activity = ActivityService(database_path)
    capture = InboxCaptureService(
        tmp_path / "vault" / "inbox", sources, SourceFragmentRepository(database_path), activity
    )
    return (
        ProvisionalIntakeService(
            tmp_path / ".steward" / "cache" / "intake",
            ProvisionalIntakeRepository(database_path),
            capture,
            activity,
        ),
        sources,
        activity,
    )


def test_file_is_staged_without_registering_a_source_until_accepted(tmp_path: Path) -> None:
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


def test_discard_removes_staged_original_without_creating_a_source(tmp_path: Path) -> None:
    service, sources, activity = make_service(tmp_path)
    original = tmp_path / "download.pdf"
    original.write_bytes(b"pdf bytes")
    intake = service.stage_file(make_event(), original)

    discarded = service.discard(intake.id or 0, "100")

    assert discarded.status == "discarded"
    assert not intake.staged_path.exists()
    assert sources.list_all() == []
    assert activity.list_recent()[0].event_type is ActivityType.INTAKE_DISCARDED


def test_another_chat_cannot_accept_a_staged_file(tmp_path: Path) -> None:
    service, _, _ = make_service(tmp_path)
    original = tmp_path / "download.pdf"
    original.write_bytes(b"pdf bytes")
    intake = service.stage_file(make_event(), original)

    with pytest.raises(ValueError, match="another chat"):
        service.accept(intake.id or 0, make_event(event_id="telegram:43", chat_id="other"))


def test_text_note_is_staged_until_the_user_accepts_it(tmp_path: Path) -> None:
    service, sources, _ = make_service(tmp_path)
    event = IncomingEvent(
        "telegram:text", "telegram", "100", "8", None, datetime(2026, 9, 9, tzinfo=UTC),
        "A note about OpenMP scheduling.",
    )

    intake = service.stage_text(event)
    assert intake.staged_path.is_file()
    saved = service.accept(intake.id or 0, make_event(event_id="telegram:accept"))

    assert saved.source.path.is_file()
    assert not intake.staged_path.exists()
    assert sources.get_by_id(saved.source.id or 0) == saved.source
