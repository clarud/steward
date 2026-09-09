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
    INTAKE_ANALYSIS_SELECTED = "intake_analysis_selected"
    INTAKE_ACCEPTED = "intake_accepted"
    INTAKE_DISCARDED = "intake_discarded"
    WORKSPACE_CREATED = "workspace_created"
    SOURCE_LINKED_TO_WORKSPACE = "source_linked_to_workspace"
    RECORD_CORRECTED = "record_corrected"
    ORGANIZATION_PROPOSED = "organization_proposed"
    ORGANIZATION_ACCEPTED = "organization_accepted"
    ORGANIZATION_REJECTED = "organization_rejected"
    SOURCE_MOVED = "source_moved"
    SOURCE_MOVE_UNDONE = "source_move_undone"
    SOURCE_REEXTRACTED = "source_reextracted"
    ACTION_PROPOSED = "action_proposed"
    ACTION_ACCEPTED = "action_accepted"
    ACTION_REJECTED = "action_rejected"
    TASK_CREATED = "task_created"
    TASK_COMPLETED = "task_completed"
    TASK_REMINDER_SENT = "task_reminder_sent"
    TELEGRAM_DELIVERY_RECOVERED = "telegram_delivery_recovered"
    SOURCE_PRIVACY_CHANGED = "source_privacy_changed"
    CALENDAR_EVENT_CREATED = "calendar_event_created"
    KNOWLEDGE_ENRICHMENT_PROPOSED = "knowledge_enrichment_proposed"
    KNOWLEDGE_ENRICHMENT_ACCEPTED = "knowledge_enrichment_accepted"
    KNOWLEDGE_ENRICHMENT_REJECTED = "knowledge_enrichment_rejected"

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
