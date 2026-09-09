"""Durable, chat-scoped Telegram callback identifiers."""

from __future__ import annotations

import secrets
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path


@dataclass(frozen=True, slots=True)
class TelegramCallback:
    """A validated, short-lived action that a Telegram button may invoke."""

    token: str
    chat_id: str
    command: str
    expires_at: datetime


class TelegramCallbackRepository:
    """Persist opaque callback tokens rather than placing commands in Telegram data.

    Telegram callback data is small and client-controlled. Keeping the command
    locally scopes it to one authorized chat and permits expiry checks after a
    process restart.
    """

    def __init__(self, database_path: Path, *, ttl_seconds: int = 900) -> None:
        if ttl_seconds <= 0:
            raise ValueError("Telegram callback TTL must be positive.")
        self._database_path = database_path
        self._ttl = timedelta(seconds=ttl_seconds)

    def create(
        self, chat_id: str, command: str, *, now: datetime | None = None
    ) -> TelegramCallback:
        if not chat_id:
            raise ValueError("Telegram callback chat ID must not be empty.")
        if not command.startswith("/"):
            raise ValueError("Telegram callback commands must start with '/'.")
        created_at = now or datetime.now(UTC)
        expires_at = created_at + self._ttl
        token = secrets.token_urlsafe(12)
        with sqlite3.connect(self._database_path) as connection:
            connection.execute(
                "INSERT INTO telegram_callbacks (token, chat_id, command, expires_at, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (token, chat_id, command, expires_at.isoformat(), created_at.isoformat()),
            )
        return TelegramCallback(token, chat_id, command, expires_at)

    def resolve(
        self, token: str, chat_id: str, *, now: datetime | None = None
    ) -> TelegramCallback | None:
        checked_at = now or datetime.now(UTC)
        with sqlite3.connect(self._database_path) as connection:
            row = connection.execute(
                "SELECT token, chat_id, command, expires_at FROM telegram_callbacks WHERE token = ?",
                (token,),
            ).fetchone()
            if row is None:
                return None
            callback = TelegramCallback(
                str(row[0]), str(row[1]), str(row[2]), datetime.fromisoformat(str(row[3]))
            )
            if callback.chat_id != chat_id:
                return None
            if callback.expires_at <= checked_at:
                connection.execute("DELETE FROM telegram_callbacks WHERE token = ?", (token,))
                return None
        return callback
