"""Small, canonical task records kept separate from Calendar events."""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from steward.activity import ActivityService, ActivityType


@dataclass(frozen=True, slots=True)
class Task:
    id: int | None
    title: str
    due_hint: str | None
    due_at: datetime | None
    status: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class TaskReminder:
    """An explicitly scheduled Telegram reminder for one open task."""

    task: Task
    chat_id: str
    remind_at: datetime
    claim_token: str | None = None


class TaskService:
    """Persist explicit commitments; scheduling remains a later concern."""

    def __init__(self, database_path: Path) -> None:
        self._database_path = database_path

    @staticmethod
    def parse_proposal(text: str) -> tuple[str, str | None]:
        """Keep a due phrase visible instead of guessing a date or timezone."""
        normalized = " ".join(text.split())
        normalized = re.sub(
            r"^(?:remind me to|remember to|don't let me forget to|i need to|i should|task:|todo:|deadline:)\s*",
            "",
            normalized,
            flags=re.I,
        )
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
    def parse_proposal_with_schedule(text: str) -> tuple[str, str | None, datetime | None, datetime | None]:
        """Parse explicit deadline/reminder instants while retaining natural-language cues.

        Dates are never inferred. Both switches require an ISO-8601 offset so
        a Telegram reminder remains unambiguous across restarts and time zones.
        """
        options = dict(re.findall(r"\s+--(due-at|remind-at)\s+(\S+)", text))
        if len(options) != len(re.findall(r"\s+--(?:due-at|remind-at)\s+\S+", text)):
            raise ValueError("Use each of --due-at and --remind-at at most once.")
        stripped = re.sub(r"\s+--(?:due-at|remind-at)\s+\S+", "", text).strip()
        title, due_hint = TaskService.parse_proposal(stripped)
        due_at = TaskService.parse_due_at(options["due-at"]) if "due-at" in options else None
        remind_at = TaskService.parse_due_at(options["remind-at"]) if "remind-at" in options else None
        return title, due_hint, due_at, remind_at

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

    def list_completed(self) -> tuple[Task, ...]:
        with sqlite3.connect(self._database_path) as connection:
            rows = connection.execute(
                "SELECT id, title, due_hint, due_at, status, created_at FROM tasks "
                "WHERE status = 'completed' ORDER BY completed_at DESC, id DESC"
            ).fetchall()
        return tuple(self._from_row(row) for row in rows)

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


class TaskReminderService:
    """Durably coordinate explicit Telegram reminders with at-least-once delivery.

    A claim is intentionally recoverable after five minutes. A process can die
    between Telegram accepting a message and local acknowledgement, so the
    system favors a possible duplicate reminder over silently losing one.
    """

    _CLAIM_TTL = timedelta(minutes=5)

    def __init__(self, database_path: Path, tasks: TaskService, activity: ActivityService) -> None:
        self._database_path = database_path
        self._tasks = tasks
        self._activity = activity

    def schedule(self, task_id: int, chat_id: str, remind_at: datetime) -> TaskReminder:
        if not chat_id.strip():
            raise ValueError("A Telegram reminder requires an originating chat.")
        if remind_at.tzinfo is None or remind_at.utcoffset() is None:
            raise ValueError("A reminder time must include a UTC offset.")
        task = self._tasks.get(task_id)
        if task is None:
            raise ValueError(f"Task {task_id} was not found.")
        if task.status != "open":
            raise ValueError(f"Task {task_id} is not open.")
        normalized = remind_at.astimezone(UTC)
        with sqlite3.connect(self._database_path) as connection:
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute(
                "INSERT OR REPLACE INTO task_reminders (task_id, chat_id, remind_at, claimed_at, reminded_at) "
                "VALUES (?, ?, ?, NULL, NULL)",
                (task_id, chat_id, normalized.isoformat()),
            )
        return TaskReminder(task, chat_id, normalized)

    def reminder_for_task(self, task_id: int) -> TaskReminder | None:
        with sqlite3.connect(self._database_path) as connection:
            row = connection.execute(
                "SELECT chat_id, remind_at FROM task_reminders WHERE task_id = ? AND reminded_at IS NULL",
                (task_id,),
            ).fetchone()
        task = self._tasks.get(task_id)
        if row is None or task is None:
            return None
        return TaskReminder(task, str(row[0]), datetime.fromisoformat(str(row[1])))

    def claim_due(self, now: datetime | None = None) -> tuple[TaskReminder, ...]:
        now = (now or datetime.now(UTC)).astimezone(UTC)
        stale_before = now - self._CLAIM_TTL
        claimed: list[TaskReminder] = []
        with sqlite3.connect(self._database_path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute(
                """
                SELECT t.id, t.title, t.due_hint, t.due_at, t.status, t.created_at,
                       r.chat_id, r.remind_at
                FROM task_reminders AS r
                JOIN tasks AS t ON t.id = r.task_id
                WHERE t.status = 'open' AND r.remind_at <= ? AND r.reminded_at IS NULL
                  AND (r.claimed_at IS NULL OR r.claimed_at < ?)
                ORDER BY r.remind_at, t.id
                """,
                (now.isoformat(), stale_before.isoformat()),
            ).fetchall()
            for row in rows:
                task_id = int(row[0])
                claim_token = uuid4().hex
                cursor = connection.execute(
                    "UPDATE task_reminders SET claimed_at = ?, claim_token = ? WHERE task_id = ? AND reminded_at IS NULL "
                    "AND (claimed_at IS NULL OR claimed_at < ?)",
                    (now.isoformat(), claim_token, task_id, stale_before.isoformat()),
                )
                if cursor.rowcount != 1:
                    continue
                task = Task(
                    task_id, str(row[1]), str(row[2]) if row[2] else None,
                    datetime.fromisoformat(str(row[3])) if row[3] else None,
                    str(row[4]), datetime.fromisoformat(str(row[5])),
                )
                claimed.append(TaskReminder(task, str(row[6]), datetime.fromisoformat(str(row[7])), claim_token))
        return tuple(claimed)

    def acknowledge(self, task_id: int, claim_token: str, now: datetime | None = None) -> None:
        occurred_at = (now or datetime.now(UTC)).astimezone(UTC)
        with sqlite3.connect(self._database_path) as connection:
            cursor = connection.execute(
                "UPDATE task_reminders SET reminded_at = ?, claimed_at = NULL, claim_token = NULL "
                "WHERE task_id = ? AND claim_token = ? AND reminded_at IS NULL",
                (occurred_at.isoformat(), task_id, claim_token),
            )
        if cursor.rowcount:
            self._activity.record(ActivityType.TASK_REMINDER_SENT, object_id=str(task_id), details="Telegram reminder delivered")

    def release(self, task_id: int, claim_token: str) -> None:
        with sqlite3.connect(self._database_path) as connection:
            connection.execute(
                "UPDATE task_reminders SET claimed_at = NULL, claim_token = NULL "
                "WHERE task_id = ? AND claim_token = ? AND reminded_at IS NULL",
                (task_id, claim_token),
            )
