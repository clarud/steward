"""Append-only audit events for durable Steward operations."""
from __future__ import annotations
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path

class ActivityType(StrEnum):
    SOURCE_CAPTURED = "source_captured"
    WORKSPACE_CREATED = "workspace_created"
    ORGANIZATION_PROPOSED = "organization_proposed"
    ORGANIZATION_ACCEPTED = "organization_accepted"
    ORGANIZATION_REJECTED = "organization_rejected"

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
