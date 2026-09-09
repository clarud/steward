"""Durable, at-least-once delivery coordination for Telegram updates."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path


@dataclass(frozen=True, slots=True)
class TelegramDelivery:
    """Metadata-only view of one locally coordinated Telegram update."""

    update_id: str
    status: str
    claimed_at: datetime
    delivered_at: datetime | None


@dataclass(frozen=True, slots=True)
class TelegramDeliveryHistoryEvent:
    update_id: str
    event_type: str
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class TelegramDeadLetter:
    update_id: str
    attempts: int
    failed_at: datetime


class TelegramUpdateDeliveryRepository:
    """Claim one Telegram update before it reaches Steward's application layer.

    Telegram may redeliver an update after a transport failure.  A successful
    reply marks the update delivered.  A failure releases its claim so a later
    Telegram retry can safely try again.  This is deliberately *at-least-once*
    processing: a process that dies between application work and the reply may
    run idempotent application work again, but it will not silently lose the
    user's message.
    """

    def __init__(
        self,
        database_path: Path,
        *,
        lease_seconds: int = 900,
        max_attempts: int = 3,
        retry_backoff_seconds: int = 15,
        max_retry_backoff_seconds: int = 300,
    ) -> None:
        if lease_seconds <= 0:
            raise ValueError("Telegram delivery lease must be positive.")
        if max_attempts <= 0:
            raise ValueError("Telegram delivery max attempts must be positive.")
        if retry_backoff_seconds <= 0:
            raise ValueError("Telegram delivery retry backoff must be positive.")
        if max_retry_backoff_seconds < retry_backoff_seconds:
            raise ValueError("Telegram delivery maximum retry backoff must not be smaller than the base backoff.")
        self._database_path = database_path
        self._lease = timedelta(seconds=lease_seconds)
        self._max_attempts = max_attempts
        self._retry_backoff_seconds = retry_backoff_seconds
        self._max_retry_backoff_seconds = max_retry_backoff_seconds

    def claim(self, update_id: str, *, now: datetime | None = None) -> bool:
        """Atomically claim an unseen or expired-processing update."""

        claimed_at = now or datetime.now(UTC)
        with sqlite3.connect(self._database_path) as connection:
            if connection.execute(
                "SELECT 1 FROM telegram_delivery_dead_letters WHERE update_id = ?", (update_id,)
            ).fetchone() is not None:
                return False
            release_row = connection.execute(
                """
                SELECT COUNT(*), MAX(occurred_at) FROM telegram_delivery_history
                WHERE update_id = ? AND event_type = 'released'
                  AND occurred_at > COALESCE(
                      (SELECT MAX(recovered_at) FROM telegram_delivery_recoveries WHERE update_id = ?),
                      ''
                  )
                """,
                (update_id, update_id),
            ).fetchone()
            releases = int(release_row[0])
            if releases >= self._max_attempts:
                connection.execute(
                    "INSERT OR IGNORE INTO telegram_delivery_dead_letters (update_id, attempts, failed_at) VALUES (?, ?, ?)",
                    (update_id, releases, claimed_at.isoformat()),
                )
                return False
            last_released_at = (
                datetime.fromisoformat(str(release_row[1])) if release_row[1] else None
            )
            if last_released_at is not None and claimed_at < last_released_at + self._backoff(releases):
                return False
            cursor = connection.execute(
                "INSERT OR IGNORE INTO telegram_update_deliveries "
                "(update_id, status, claimed_at) VALUES (?, 'processing', ?)",
                (update_id, claimed_at.isoformat()),
            )
            if cursor.rowcount == 1:
                self._record_history(connection, update_id, "claimed", claimed_at)
                return True
            cursor = connection.execute(
                "UPDATE telegram_update_deliveries SET claimed_at = ? "
                "WHERE update_id = ? AND status = 'processing' AND claimed_at <= ?",
                (claimed_at.isoformat(), update_id, (claimed_at - self._lease).isoformat()),
            )
            if cursor.rowcount == 1:
                self._record_history(connection, update_id, "reclaimed", claimed_at)
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
            if cursor.rowcount == 1:
                self._record_history(connection, update_id, "delivered", datetime.now(UTC))
        if cursor.rowcount != 1:
            raise ValueError(f"Telegram update {update_id!r} is not being processed.")

    def release(self, update_id: str, *, now: datetime | None = None) -> None:
        """Make a failed delivery eligible for Telegram's next retry."""

        with sqlite3.connect(self._database_path) as connection:
            cursor = connection.execute(
                "DELETE FROM telegram_update_deliveries "
                "WHERE update_id = ? AND status = 'processing'",
                (update_id,),
            )
            if cursor.rowcount == 1:
                self._record_history(connection, update_id, "released", now or datetime.now(UTC))

    def reopen_dead_letter(self, update_id: str, *, now: datetime | None = None) -> None:
        """Reset a terminal retry budget for a future Telegram redelivery.

        This does not, and cannot, replay the original update: delivery storage
        intentionally retains no Telegram message body. A later genuine
        redelivery is eligible for a fresh, separately auditable retry budget.
        """

        recovered_at = now or datetime.now(UTC)
        with sqlite3.connect(self._database_path) as connection:
            cursor = connection.execute(
                "DELETE FROM telegram_delivery_dead_letters WHERE update_id = ?", (update_id,)
            )
            if cursor.rowcount != 1:
                raise ValueError(f"Telegram update {update_id!r} is not a dead letter.")
            connection.execute(
                "INSERT INTO telegram_delivery_recoveries (update_id, recovered_at) VALUES (?, ?)",
                (update_id, recovered_at.isoformat()),
            )

    def _backoff(self, releases: int) -> timedelta:
        """Return a capped exponential delay after one or more failed attempts."""

        seconds = min(
            self._retry_backoff_seconds * (2 ** max(0, releases - 1)),
            self._max_retry_backoff_seconds,
        )
        return timedelta(seconds=seconds)

    @staticmethod
    def _record_history(
        connection: sqlite3.Connection, update_id: str, event_type: str, occurred_at: datetime
    ) -> None:
        connection.execute(
            "INSERT INTO telegram_delivery_history (update_id, event_type, occurred_at) VALUES (?, ?, ?)",
            (update_id, event_type, occurred_at.isoformat()),
        )

    def list_recent(self, *, limit: int = 20) -> tuple[TelegramDelivery, ...]:
        """Inspect recent local delivery state without retaining message content."""
        if not 1 <= limit <= 100:
            raise ValueError("Telegram delivery limit must be between 1 and 100.")
        with sqlite3.connect(self._database_path) as connection:
            rows = connection.execute(
                "SELECT update_id, status, claimed_at, delivered_at "
                "FROM telegram_update_deliveries ORDER BY claimed_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return tuple(
            TelegramDelivery(str(row[0]), str(row[1]), datetime.fromisoformat(str(row[2])),
                             datetime.fromisoformat(str(row[3])) if row[3] else None)
            for row in rows
        )

    def list_history(self, *, limit: int = 50) -> tuple[TelegramDeliveryHistoryEvent, ...]:
        if not 1 <= limit <= 100:
            raise ValueError("Telegram delivery history limit must be between 1 and 100.")
        with sqlite3.connect(self._database_path) as connection:
            rows = connection.execute(
                "SELECT update_id, event_type, occurred_at FROM telegram_delivery_history "
                "ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return tuple(
            TelegramDeliveryHistoryEvent(str(row[0]), str(row[1]), datetime.fromisoformat(str(row[2])))
            for row in rows
        )

    def list_dead_letters(self, *, limit: int = 50) -> tuple[TelegramDeadLetter, ...]:
        """Inspect terminal local failures without retaining a message body."""
        if not 1 <= limit <= 100:
            raise ValueError("Telegram dead-letter limit must be between 1 and 100.")
        with sqlite3.connect(self._database_path) as connection:
            rows = connection.execute(
                "SELECT update_id, attempts, failed_at FROM telegram_delivery_dead_letters ORDER BY failed_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return tuple(TelegramDeadLetter(str(row[0]), int(row[1]), datetime.fromisoformat(str(row[2]))) for row in rows)

    def get_dead_letter(self, update_id: str) -> TelegramDeadLetter | None:
        """Return terminal metadata for one update, never its original body."""

        with sqlite3.connect(self._database_path) as connection:
            row = connection.execute(
                "SELECT update_id, attempts, failed_at FROM telegram_delivery_dead_letters WHERE update_id = ?",
                (update_id,),
            ).fetchone()
        return (
            TelegramDeadLetter(str(row[0]), int(row[1]), datetime.fromisoformat(str(row[2])))
            if row is not None
            else None
        )
