"""Append-only audit events for durable Steward operations."""
from __future__ import annotations
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path

class ActivityType(StrEnum):
    SOURCE_CAPTURED = "source_captured"
    SOURCE_UNREGISTERED = "source_unregistered"
    INTAKE_PROPOSED = "intake_proposed"
    INTAKE_REVISED = "intake_revised"
    INTAKE_ACCEPTED = "intake_accepted"
    INTAKE_DISCARDED = "intake_discarded"

    @classmethod
    def _missing_(cls, value: object) -> "ActivityType | None":
        """Keep audit rows written by retired features readable.

        The audit log is append-only, so older databases may contain event
        types that current code never emits. They load as pseudo-members that
        preserve the stored value for display and counting.
        """
        if not isinstance(value, str) or not value:
            return None
        member = str.__new__(cls, value)
        member._name_ = value.upper()
        member._value_ = value
        return member

@dataclass(frozen=True, slots=True)
class ActivityEvent:
    id: int | None
    event_type: ActivityType
    object_id: str | None
    details: str
    occurred_at: datetime

class ActivityService:
    def __init__(self, database_path: Path) -> None: self._database_path = database_path
    def record(self, event_type: ActivityType, *, object_id: str | None = None, details: str = "") -> ActivityEvent:
        event = ActivityEvent(None, event_type, object_id, details, datetime.now(UTC))
        with sqlite3.connect(self._database_path) as connection:
            cursor = connection.execute("INSERT INTO activity_events (event_type,object_id,details,occurred_at) VALUES (?,?,?,?)", (event.event_type.value,event.object_id,event.details,event.occurred_at.isoformat()))
        return ActivityEvent(int(cursor.lastrowid), event.event_type,event.object_id,event.details,event.occurred_at)
    def list_recent(self, limit: int = 20) -> list[ActivityEvent]:
        with sqlite3.connect(self._database_path) as connection:
            rows=connection.execute("SELECT id,event_type,object_id,details,occurred_at FROM activity_events ORDER BY id DESC LIMIT ?",(limit,)).fetchall()
        return [ActivityEvent(int(r[0]),ActivityType(str(r[1])),str(r[2]) if r[2] else None,str(r[3]),datetime.fromisoformat(str(r[4]))) for r in rows]

    def get(self, event_id: int) -> ActivityEvent | None:
        """Return one local audit event by its opaque database ID."""

        if event_id <= 0:
            return None
        with sqlite3.connect(self._database_path) as connection:
            row = connection.execute(
                "SELECT id,event_type,object_id,details,occurred_at FROM activity_events WHERE id = ?",
                (event_id,),
            ).fetchone()
        if row is None:
            return None
        return ActivityEvent(
            int(row[0]), ActivityType(str(row[1])), str(row[2]) if row[2] else None,
            str(row[3]), datetime.fromisoformat(str(row[4])),
        )

    def counts(self) -> dict[ActivityType, int]:
        """Return aggregate audit counts without reading event details."""
        with sqlite3.connect(self._database_path) as connection:
            rows = connection.execute(
                "SELECT event_type, COUNT(*) FROM activity_events GROUP BY event_type"
            ).fetchall()
        return {ActivityType(str(event_type)): int(count) for event_type, count in rows}
