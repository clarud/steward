from datetime import UTC, datetime
from pathlib import Path

import pytest

from steward.activity import ActivityService, ActivityType
from steward.capture import InboxCaptureService
from steward.events import IncomingEvent
from steward.extraction import SourceFragmentRepository
from steward.intake import IntakeAnalysisMode, ProvisionalIntakeRepository, ProvisionalIntakeService
from steward.privacy import PrivacyRule, PrivacyService
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
            PrivacyService(database_path),
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
    assert intake.category == "document"
    assert intake.analysis_mode is IntakeAnalysisMode.NONE
    assert "No content was sent to a model" in intake.summary

    saved = service.accept(intake.id or 0, event)

    assert saved.duplicate is False
    assert saved.source.path.is_file()
    assert not intake.staged_path.exists()
    assert sources.list_all() == [saved.source]
    assert PrivacyService(tmp_path / "steward.db").rule_for(saved.source.id or 0) is PrivacyRule.NO_MODEL
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


def test_context_revision_is_audited_without_saving_the_staged_file(tmp_path: Path) -> None:
    service, sources, activity = make_service(tmp_path)
    original = tmp_path / "download.pdf"
    original.write_bytes(b"pdf bytes")
    intake = service.stage_file(make_event(), original)

    revised = service.add_context(intake.id or 0, "100", "CS3210 OpenMP assignment")

    assert "CS3210 OpenMP assignment" in revised.summary
    assert intake.staged_path.is_file()
    assert sources.list_all() == []
    assert activity.list_recent()[0].event_type is ActivityType.INTAKE_REVISED


def test_user_selects_an_analysis_boundary_before_capture_and_it_controls_source_privacy(tmp_path: Path) -> None:
    service, sources, activity = make_service(tmp_path)
    original = tmp_path / "notes.pdf"; original.write_bytes(b"pdf bytes")
    intake = service.stage_file(make_event(), original)

    selected = service.set_analysis_mode(
        intake.id or 0, "100", IntakeAnalysisMode.EXTERNAL
    )
    assert ProvisionalIntakeRepository(tmp_path / "steward.db").get(intake.id or 0) == selected
    saved = service.accept(intake.id or 0, make_event(event_id="telegram:accept"))

    assert selected.analysis_mode is IntakeAnalysisMode.EXTERNAL
    assert PrivacyService(tmp_path / "steward.db").rule_for(saved.source.id or 0) is PrivacyRule.EXTERNAL_ALLOWED
    assert sources.list_all() == [saved.source]
    assert [event.event_type for event in activity.list_recent()] == [
        ActivityType.INTAKE_ACCEPTED,
        ActivityType.SOURCE_CAPTURED,
        ActivityType.INTAKE_ANALYSIS_SELECTED,
        ActivityType.INTAKE_PROPOSED,
    ]


def test_another_chat_cannot_change_a_pending_intake_analysis_boundary(tmp_path: Path) -> None:
    service, _, _ = make_service(tmp_path)
    original = tmp_path / "notes.pdf"; original.write_bytes(b"pdf bytes")
    intake = service.stage_file(make_event(), original)

    with pytest.raises(ValueError, match="another chat"):
        service.set_analysis_mode(intake.id or 0, "other", IntakeAnalysisMode.LOCAL)


def test_text_intake_classifies_task_and_record_cues_without_a_model(tmp_path: Path) -> None:
    service, _, _ = make_service(tmp_path)
    task_event = IncomingEvent(
        "telegram:task", "telegram", "100", "9", None, datetime(2026, 9, 9, tzinfo=UTC),
        "Deadline: submit OpenMP work before Tuesday.",
    )
    record_event = IncomingEvent(
        "telegram:record", "telegram", "100", "10", None, datetime(2026, 9, 9, tzinfo=UTC),
        "Flight SQ638 booking reference ABC.",
    )

    task = service.stage_text(task_event)
    record = service.stage_text(record_event)

    assert task.category == "task"
    assert record.category == "record"


def test_duplicate_delivery_reuses_one_pending_intake_without_a_second_capture(tmp_path: Path) -> None:
    service, sources, activity = make_service(tmp_path)
    original = tmp_path / "download.pdf"
    original.write_bytes(b"pdf bytes")
    event = make_event()

    first = service.stage_file(event, original)
    second = service.stage_file(event, original)

    assert second == first
    assert sources.list_all() == []
    assert [item.event_type for item in activity.list_recent()] == [ActivityType.INTAKE_PROPOSED]


def test_pending_intake_can_be_accepted_after_service_restart(tmp_path: Path) -> None:
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
        PrivacyService(database_path),
    )

    saved = restarted.accept(intake.id or 0, make_event(event_id="telegram:accept"))

    assert saved.source.path.is_file()
    assert not intake.staged_path.exists()
