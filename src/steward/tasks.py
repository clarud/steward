"""Small, canonical task records kept separate from Calendar events."""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path


@dataclass(frozen=True, slots=True)
class Task:
    id: int | None
    title: str
    due_hint: str | None
    due_at: datetime | None
    status: str
    created_at: datetime


class TaskService:
    """Persist explicit commitments; scheduling remains a later concern."""

    def __init__(self, database_path: Path) -> None:
        self._database_path = database_path

    @staticmethod
    def parse_proposal(text: str) -> tuple[str, str | None]:
        """Keep a due phrase visible instead of guessing a date or timezone."""
        normalized = " ".join(text.split())
        normalized = re.sub(r"^(?:remind me to|task:|todo:|deadline:)\s*", "", normalized, flags=re.I)
        if not normalized:
            raise ValueError("Tell me the task you want Steward to propose.")
        match = re.search(r"\s+((?:by|before|on|due)\s+.+)$", normalized, flags=re.I)
        if match is None:
            return normalized, None
        title = normalized[:match.start()].strip(" ,.-")
        if not title:
            raise ValueError("Tell me what needs to be done before giving a due time.")
        return title, match.group(1)

    @staticmethod
    def parse_proposal_with_due_at(text: str) -> tuple[str, str | None, datetime | None]:
        """Parse an optional explicit, offset-aware deadline without guessing one.

        Natural language such as ``before Tuesday`` remains a visible cue. A
        deadline becomes a scheduleable instant only when the user supplies
        ``--due-at 2026-09-15T09:00:00+08:00`` with an explicit UTC offset.
        """

        match = re.search(r"\s+--due-at\s+(\S+)\s*$", text)
        if match is None:
            return (*TaskService.parse_proposal(text), None)
        due_at = TaskService.parse_due_at(match.group(1))
        return (*TaskService.parse_proposal(text[:match.start()]), due_at)

    @staticmethod
    def parse_due_at(value: str) -> datetime:
        """Validate a precise ISO-8601 deadline and normalize it to UTC."""

        try:
            due_at = datetime.fromisoformat(value)
        except ValueError as error:
            raise ValueError("--due-at must be an ISO-8601 timestamp with a UTC offset.") from error
        if due_at.tzinfo is None or due_at.utcoffset() is None:
            raise ValueError("--due-at must include a UTC offset, for example +08:00.")
        return due_at.astimezone(UTC)

    def create(self, title: str, due_hint: str | None = None, due_at: datetime | None = None) -> Task:
        title = " ".join(title.split())
        due_hint = " ".join(due_hint.split()) if due_hint else None
        if not title:
            raise ValueError("A task title must not be empty.")
        if due_at is not None and (due_at.tzinfo is None or due_at.utcoffset() is None):
            raise ValueError("A task deadline must include a UTC offset.")
        normalized_due_at = due_at.astimezone(UTC) if due_at is not None else None
        created_at = datetime.now(UTC)
        with sqlite3.connect(self._database_path) as connection:
            cursor = connection.execute(
                "INSERT INTO tasks (title, due_hint, due_at, status, created_at) VALUES (?, ?, ?, 'open', ?)",
                (title, due_hint, normalized_due_at.isoformat() if normalized_due_at else None, created_at.isoformat()),
            )
        return Task(int(cursor.lastrowid), title, due_hint, normalized_due_at, "open", created_at)

    def list_open(self) -> tuple[Task, ...]:
        with sqlite3.connect(self._database_path) as connection:
            rows = connection.execute(
                "SELECT id, title, due_hint, due_at, status, created_at FROM tasks "
                "WHERE status = 'open' ORDER BY due_at IS NULL, due_at, id"
            ).fetchall()
        return tuple(
            self._from_row(row)
            for row in rows
        )

    def complete(self, task_id: int) -> Task:
        if task_id <= 0:
            raise ValueError("Task ID must be positive.")
        with sqlite3.connect(self._database_path) as connection:
            row = connection.execute(
                "SELECT id, title, due_hint, due_at, status, created_at FROM tasks WHERE id = ?", (task_id,)
            ).fetchone()
            if row is None:
                raise ValueError(f"Task {task_id} was not found.")
            task = self._from_row(row)
            if task.status == "completed":
                return task
            connection.execute("UPDATE tasks SET status = 'completed', completed_at = ? WHERE id = ?", (datetime.now(UTC).isoformat(), task_id))
        return Task(task.id, task.title, task.due_hint, task.due_at, "completed", task.created_at)

    def get(self, task_id: int) -> Task | None:
        with sqlite3.connect(self._database_path) as connection:
            row = connection.execute(
                "SELECT id, title, due_hint, due_at, status, created_at FROM tasks WHERE id = ?", (task_id,)
            ).fetchone()
        return (
            self._from_row(row)
            if row is not None else None
        )

    @staticmethod
    def _from_row(row: tuple[object, ...]) -> Task:
        return Task(
            int(row[0]),
            str(row[1]),
            str(row[2]) if row[2] else None,
            datetime.fromisoformat(str(row[3])) if row[3] else None,
            str(row[4]),
            datetime.fromisoformat(str(row[5])),
        )
