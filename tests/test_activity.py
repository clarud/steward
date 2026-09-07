from pathlib import Path
from steward.activity import ActivityService, ActivityType
from steward.storage import initialize_database

def test_activity_events_are_append_only_and_recent_first(tmp_path: Path) -> None:
    database = tmp_path / "steward.db"; initialize_database(database)
    service = ActivityService(database)
    first = service.record(ActivityType.SOURCE_CAPTURED, object_id="1", details="a.md")
    second = service.record(ActivityType.WORKSPACE_CREATED, object_id="2", details="Steward")
    assert [event.id for event in service.list_recent()] == [second.id, first.id]
