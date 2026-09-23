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
    # Most Steward-owned objects use integer IDs. External systems use opaque
    # string IDs, so a durable conversational reference must support both
    # without turning either into a filesystem capability.
    identifier: int | str
    updated_at: datetime


class ReviewContextRepository:
    """Store only an opaque review reference, never message or source content."""

    def __init__(self, database_path: Path) -> None:
        self._database_path = database_path

    def set(self, platform: str, chat_id: str, kind: str, identifier: int | str) -> ReviewContext:
        if not platform or not chat_id or not kind:
            raise ValueError("A review context requires a platform, chat, kind, and identifier.")
        if isinstance(identifier, int):
            if identifier <= 0:
                raise ValueError("A numeric review context ID must be positive.")
        elif not identifier.strip():
            raise ValueError("A review context identifier must not be empty.")
        updated_at = datetime.now(UTC)
        # The legacy column has INTEGER affinity. Prefix strings so SQLite
        # cannot coerce digit-only external IDs and discard leading zeroes.
        stored_identifier = f"text:{identifier}" if isinstance(identifier, str) else identifier
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
                (platform, chat_id, kind, stored_identifier, updated_at.isoformat()),
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
        raw_identifier = str(row[3])
        if raw_identifier.startswith("text:"):
            identifier: int | str = raw_identifier[len("text:"):]
        else:
            identifier = int(raw_identifier) if raw_identifier.isdigit() else raw_identifier
        return ReviewContext(str(row[0]), str(row[1]), str(row[2]), identifier, datetime.fromisoformat(str(row[4])))

    def clear(self, platform: str, chat_id: str) -> None:
        with sqlite3.connect(self._database_path) as connection:
            connection.execute(
                "DELETE FROM telegram_review_context WHERE platform = ? AND chat_id = ?",
                (platform, chat_id),
            )


@dataclass(frozen=True, slots=True)
class MessageReference:
    platform: str
    chat_id: str
    message_id: str
    kind: str
    identifier: int | str
    created_at: datetime


class MessageReferenceRepository:
    """Map delivered transport messages to bounded, content-free object pointers."""

    def __init__(self, database_path: Path, *, retained_per_chat: int = 500) -> None:
        if retained_per_chat <= 0:
            raise ValueError("Retained message-reference count must be positive.")
        self._database_path = database_path
        self._retained_per_chat = retained_per_chat

    def set(self, platform: str, chat_id: str, message_id: str, kind: str, identifier: int | str) -> MessageReference:
        if not all((platform, chat_id, message_id, kind)):
            raise ValueError("A message reference requires platform, chat, message, kind, and identifier.")
        if isinstance(identifier, int) and identifier <= 0:
            raise ValueError("A numeric message-reference ID must be positive.")
        if isinstance(identifier, str) and not identifier.strip():
            raise ValueError("A message-reference identifier must not be empty.")
        created_at = datetime.now(UTC)
        stored = f"int:{identifier}" if isinstance(identifier, int) else f"text:{identifier}"
        with sqlite3.connect(self._database_path) as connection:
            connection.execute(
                """INSERT INTO telegram_message_references
                   (platform, chat_id, message_id, reference_kind, reference_id, created_at)
                   VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT(platform, chat_id, message_id) DO UPDATE SET
                     reference_kind = excluded.reference_kind,
                     reference_id = excluded.reference_id,
                     created_at = excluded.created_at""",
                (platform, chat_id, message_id, kind, stored, created_at.isoformat()),
            )
            connection.execute(
                """DELETE FROM telegram_message_references WHERE rowid IN (
                     SELECT rowid FROM telegram_message_references
                     WHERE platform = ? AND chat_id = ?
                     ORDER BY created_at DESC, rowid DESC LIMIT -1 OFFSET ?
                   )""",
                (platform, chat_id, self._retained_per_chat),
            )
        return MessageReference(platform, chat_id, message_id, kind, identifier, created_at)

    def get(self, platform: str, chat_id: str, message_id: str) -> MessageReference | None:
        with sqlite3.connect(self._database_path) as connection:
            row = connection.execute(
                """SELECT platform, chat_id, message_id, reference_kind, reference_id, created_at
                   FROM telegram_message_references
                   WHERE platform = ? AND chat_id = ? AND message_id = ?""",
                (platform, chat_id, message_id),
            ).fetchone()
        if row is None:
            return None
        stored = str(row[4])
        identifier: int | str = int(stored[4:]) if stored.startswith("int:") else stored.removeprefix("text:")
        return MessageReference(str(row[0]), str(row[1]), str(row[2]), str(row[3]), identifier,
                                datetime.fromisoformat(str(row[5])))
