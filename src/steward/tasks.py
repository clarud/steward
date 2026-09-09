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
        normalized = re.sub(r"^(?:remind me to|task:|todo:)\s*", "", normalized, flags=re.I)
        if not normalized:
            raise ValueError("Tell me the task you want Steward to propose.")
        match = re.search(r"\s+((?:by|before|on)\s+.+)$", normalized, flags=re.I)
        if match is None:
            return normalized, None
        title = normalized[:match.start()].strip(" ,.-")
        if not title:
            raise ValueError("Tell me what needs to be done before giving a due time.")
        return title, match.group(1)

    def create(self, title: str, due_hint: str | None = None) -> Task:
        title = " ".join(title.split())
        due_hint = " ".join(due_hint.split()) if due_hint else None
        if not title:
            raise ValueError("A task title must not be empty.")
        created_at = datetime.now(UTC)
        with sqlite3.connect(self._database_path) as connection:
            cursor = connection.execute(
                "INSERT INTO tasks (title, due_hint, status, created_at) VALUES (?, ?, 'open', ?)",
                (title, due_hint, created_at.isoformat()),
            )
        return Task(int(cursor.lastrowid), title, due_hint, "open", created_at)

    def list_open(self) -> tuple[Task, ...]:
        with sqlite3.connect(self._database_path) as connection:
            rows = connection.execute(
                "SELECT id, title, due_hint, status, created_at FROM tasks "
                "WHERE status = 'open' ORDER BY id"
            ).fetchall()
        return tuple(
            Task(int(row[0]), str(row[1]), str(row[2]) if row[2] else None, str(row[3]), datetime.fromisoformat(str(row[4])))
            for row in rows
        )
