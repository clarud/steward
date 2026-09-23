"""Local metadata describing where an Inbox capture may later belong."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


@dataclass(frozen=True, slots=True)
class SourceInboxContext:
    """Optional user-supplied routing context for an Inbox source.

    It is descriptive metadata only: it does not move the original or grant
    any filesystem authority to Steward, a model, or Codex.
    """

    source_id: int
    intended_root_id: int | None
    intended_root_name: str | None
    user_context: str | None
    capture_origin: str
    created_at: datetime


class SourceInboxContextRepository:
    """Persist one local capture-context record for each Inbox source."""

    def __init__(self, database_path: Path) -> None:
        self._database_path = database_path

    def set(self, context: SourceInboxContext) -> None:
        with sqlite3.connect(self._database_path) as connection:
            connection.execute(
                """INSERT INTO source_inbox_contexts (
                    source_id, intended_root_id, intended_root_name, user_context, capture_origin, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(source_id) DO UPDATE SET
                    intended_root_id = excluded.intended_root_id,
                    intended_root_name = excluded.intended_root_name,
                    user_context = excluded.user_context,
                    capture_origin = excluded.capture_origin,
                    created_at = excluded.created_at""",
                (
                    context.source_id, context.intended_root_id, context.intended_root_name,
                    context.user_context, context.capture_origin, context.created_at.isoformat(),
                ),
            )

    def get(self, source_id: int) -> SourceInboxContext | None:
        with sqlite3.connect(self._database_path) as connection:
            row = connection.execute(
                """SELECT source_id, intended_root_id, intended_root_name, user_context, capture_origin, created_at
                   FROM source_inbox_contexts WHERE source_id = ?""",
                (source_id,),
            ).fetchone()
        if row is None:
            return None
        return SourceInboxContext(
            source_id=int(row[0]), intended_root_id=int(row[1]) if row[1] is not None else None,
            intended_root_name=str(row[2]) if row[2] is not None else None,
            user_context=str(row[3]) if row[3] is not None else None,
            capture_origin=str(row[4]), created_at=datetime.fromisoformat(str(row[5])),
        )
