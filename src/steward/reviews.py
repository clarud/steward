"""Durable, chat-scoped pointers to a user-visible Steward review card."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path


@dataclass(frozen=True, slots=True)
class ReviewContext:
    platform: str
    chat_id: str
    kind: str
    identifier: int
    updated_at: datetime


class ReviewContextRepository:
    """Store only an opaque review reference, never message or source content."""

    def __init__(self, database_path: Path) -> None:
        self._database_path = database_path

    def set(self, platform: str, chat_id: str, kind: str, identifier: int) -> ReviewContext:
        if not platform or not chat_id or not kind or identifier <= 0:
            raise ValueError("A review context requires a platform, chat, kind, and positive ID.")
        updated_at = datetime.now(UTC)
        with sqlite3.connect(self._database_path) as connection:
            connection.execute(
                """
                INSERT INTO telegram_review_context (platform, chat_id, review_kind, review_id, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(platform, chat_id) DO UPDATE SET
                    review_kind = excluded.review_kind,
                    review_id = excluded.review_id,
                    updated_at = excluded.updated_at
                """,
                (platform, chat_id, kind, identifier, updated_at.isoformat()),
            )
        return ReviewContext(platform, chat_id, kind, identifier, updated_at)

    def get(self, platform: str, chat_id: str) -> ReviewContext | None:
        with sqlite3.connect(self._database_path) as connection:
            row = connection.execute(
                """SELECT platform, chat_id, review_kind, review_id, updated_at
                   FROM telegram_review_context WHERE platform = ? AND chat_id = ?""",
                (platform, chat_id),
            ).fetchone()
        if row is None:
            return None
        return ReviewContext(str(row[0]), str(row[1]), str(row[2]), int(row[3]), datetime.fromisoformat(str(row[4])))

    def clear(self, platform: str, chat_id: str) -> None:
        with sqlite3.connect(self._database_path) as connection:
            connection.execute(
                "DELETE FROM telegram_review_context WHERE platform = ? AND chat_id = ?",
                (platform, chat_id),
            )
