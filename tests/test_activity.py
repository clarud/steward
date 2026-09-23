import sqlite3
from pathlib import Path
from steward.activity import ActivityService, ActivityType
from steward.storage import initialize_database

def test_activity_events_are_append_only_and_recent_first(tmp_path: Path) -> None:
    database = tmp_path / "steward.db"; initialize_database(database)
    service = ActivityService(database)
    first = service.record(ActivityType.SOURCE_CAPTURED, object_id="1", details="a.md")
    second = service.record(ActivityType.INTAKE_ACCEPTED, object_id="2", details="Steward")
    assert [event.id for event in service.list_recent()] == [second.id, first.id]

def test_activity_from_retired_features_stays_readable(tmp_path: Path) -> None:
    database = tmp_path / "steward.db"; initialize_database(database)
    with sqlite3.connect(database) as connection:
        connection.execute(
            "INSERT INTO activity_events (event_type, object_id, details, occurred_at) VALUES (?, ?, ?, ?)",
            ("task_created", "7", "Old task", "2026-09-01T00:00:00+00:00"),
        )
    service = ActivityService(database)

    [event] = service.list_recent()

    assert event.event_type.value == "task_created"
    assert service.get(event.id or 0).event_type == "task_created"
    assert service.counts() == {ActivityType("task_created"): 1}
