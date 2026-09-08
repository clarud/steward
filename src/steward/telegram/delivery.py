"""Durable, at-least-once delivery coordination for Telegram updates."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path


class TelegramUpdateDeliveryRepository:
    """Claim one Telegram update before it reaches Steward's application layer.

    Telegram may redeliver an update after a transport failure.  A successful
    reply marks the update delivered.  A failure releases its claim so a later
    Telegram retry can safely try again.  This is deliberately *at-least-once*
    processing: a process that dies between application work and the reply may
    run idempotent application work again, but it will not silently lose the
    user's message.
    """

    def __init__(self, database_path: Path, *, lease_seconds: int = 900) -> None:
        if lease_seconds <= 0:
            raise ValueError("Telegram delivery lease must be positive.")
        self._database_path = database_path
        self._lease = timedelta(seconds=lease_seconds)

    def claim(self, update_id: str, *, now: datetime | None = None) -> bool:
        """Atomically claim an unseen or expired-processing update."""

        claimed_at = now or datetime.now(UTC)
        with sqlite3.connect(self._database_path) as connection:
            cursor = connection.execute(
                "INSERT OR IGNORE INTO telegram_update_deliveries "
                "(update_id, status, claimed_at) VALUES (?, 'processing', ?)",
                (update_id, claimed_at.isoformat()),
            )
            if cursor.rowcount == 1:
                return True
            cursor = connection.execute(
                "UPDATE telegram_update_deliveries SET claimed_at = ? "
                "WHERE update_id = ? AND status = 'processing' AND claimed_at <= ?",
                (claimed_at.isoformat(), update_id, (claimed_at - self._lease).isoformat()),
            )
        return cursor.rowcount == 1

    def mark_delivered(self, update_id: str) -> None:
        """Record that Steward produced a reply for an owned update."""

        with sqlite3.connect(self._database_path) as connection:
            cursor = connection.execute(
                "UPDATE telegram_update_deliveries "
                "SET status = 'delivered', delivered_at = ? "
                "WHERE update_id = ? AND status = 'processing'",
                (datetime.now(UTC).isoformat(), update_id),
            )
        if cursor.rowcount != 1:
            raise ValueError(f"Telegram update {update_id!r} is not being processed.")

    def release(self, update_id: str) -> None:
        """Make a failed delivery eligible for Telegram's next retry."""

        with sqlite3.connect(self._database_path) as connection:
            connection.execute(
                "DELETE FROM telegram_update_deliveries "
                "WHERE update_id = ? AND status = 'processing'",
                (update_id,),
            )
