"""Answer each Telegram update at most once per successful delivery."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path


class TelegramUpdateDeliveryRepository:
    """Claim one Telegram update before it reaches Steward's application layer.

    Telegram may redeliver an update after a transport failure. A successful
    reply marks the update delivered, so duplicates are ignored. A failure
    releases the claim so Telegram's next retry can try again. A claim left by
    a crashed process expires after the lease. This is at-least-once
    processing: application work must be idempotent.
    """

    def __init__(self, database_path: Path, *, lease_seconds: int = 900) -> None:
        if lease_seconds <= 0:
            raise ValueError("Telegram delivery lease must be positive.")
        self._database_path = database_path
        self._lease = timedelta(seconds=lease_seconds)

    def claim(self, update_id: str, *, now: datetime | None = None) -> bool:
        """Atomically claim an unseen update, or one whose processing lease expired."""

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
        """Record that Steward replied to an update it owns."""

        with sqlite3.connect(self._database_path) as connection:
            cursor = connection.execute(
                "UPDATE telegram_update_deliveries SET status = 'delivered', delivered_at = ? "
                "WHERE update_id = ? AND status = 'processing'",
                (datetime.now(UTC).isoformat(), update_id),
            )
        if cursor.rowcount != 1:
            raise ValueError(f"Telegram update {update_id!r} is not being processed.")

    def release(self, update_id: str) -> None:
        """Make a failed update eligible for Telegram's next retry."""

        with sqlite3.connect(self._database_path) as connection:
            connection.execute(
                "DELETE FROM telegram_update_deliveries WHERE update_id = ? AND status = 'processing'",
                (update_id,),
            )
