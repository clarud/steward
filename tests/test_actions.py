from pathlib import Path
from steward.actions import FileMutationService
from steward.activity import ActivityService, ActivityType
from steward.storage import initialize_database

def test_move_returns_rollback_data_and_undo_restores_original(tmp_path: Path) -> None:
    original = tmp_path / "inbox" / "note.md"; original.parent.mkdir(); original.write_text("note")
    destination = tmp_path / "projects" / "Steward" / "note.md"
    service = FileMutationService()
    result = service.move_source(original, destination)
    assert destination.is_file() and not original.exists()
    undone = service.undo_move(result)
    assert original.is_file() and undone.status == "undone"


def test_undo_records_a_distinct_audit_event(tmp_path: Path) -> None:
    database = tmp_path / "steward.db"
    initialize_database(database)
    original = tmp_path / "inbox" / "note.md"; original.parent.mkdir(); original.write_text("note")
    destination = tmp_path / "projects" / "Steward" / "note.md"
    activity = ActivityService(database)

    result = FileMutationService(activity_service=activity).move_source(original, destination)
    undone = FileMutationService(activity_service=activity).undo_move(result)

    assert undone.activity_event_id is not None
    assert [event.event_type for event in activity.list_recent()] == [
        ActivityType.SOURCE_MOVE_UNDONE,
        ActivityType.SOURCE_MOVED,
    ]
