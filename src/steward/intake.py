"""Reviewable temporary intake before material becomes a durable Source."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from shutil import copy2

from steward.activity import ActivityService, ActivityType
from steward.capture import CaptureResult, InboxCaptureService
from steward.events import IncomingEvent
from steward.sources import source_type_for_path


@dataclass(frozen=True, slots=True)
class ProvisionalIntake:
    """A locally staged item awaiting an explicit save-or-discard decision."""

    id: int | None
    event_id: str
    platform: str
    chat_id: str
    message_id: str
    kind: str
    staged_path: Path
    original_name: str
    summary: str
    status: str
    created_at: datetime
    decided_at: datetime | None = None


class ProvisionalIntakeRepository:
    """Durably track staged files so a restart cannot lose a pending decision."""

    def __init__(self, database_path: Path) -> None:
        self._database_path = database_path

    def add(self, intake: ProvisionalIntake) -> ProvisionalIntake:
        if intake.id is not None:
            raise ValueError("Only an unstored provisional intake can be added.")
        with sqlite3.connect(self._database_path) as connection:
            cursor = connection.execute(
                """
                INSERT INTO provisional_intakes (
                    event_id, platform, chat_id, message_id, kind, staged_path,
                    original_name, summary, status, created_at, decided_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    intake.event_id, intake.platform, intake.chat_id, intake.message_id,
                    intake.kind, str(intake.staged_path), intake.original_name, intake.summary,
                    intake.status, intake.created_at.isoformat(),
                    intake.decided_at.isoformat() if intake.decided_at else None,
                ),
            )
        return replace(intake, id=int(cursor.lastrowid))

    def get(self, intake_id: int) -> ProvisionalIntake | None:
        with sqlite3.connect(self._database_path) as connection:
            row = connection.execute(
                """
                SELECT id, event_id, platform, chat_id, message_id, kind, staged_path,
                       original_name, summary, status, created_at, decided_at
                FROM provisional_intakes WHERE id = ?
                """,
                (intake_id,),
            ).fetchone()
        return self._from_row(row) if row is not None else None

    def get_by_event_id(self, event_id: str) -> ProvisionalIntake | None:
        with sqlite3.connect(self._database_path) as connection:
            row = connection.execute(
                """
                SELECT id, event_id, platform, chat_id, message_id, kind, staged_path,
                       original_name, summary, status, created_at, decided_at
                FROM provisional_intakes WHERE event_id = ?
                """,
                (event_id,),
            ).fetchone()
        return self._from_row(row) if row is not None else None

    def decide(self, intake_id: int, status: str) -> ProvisionalIntake:
        if status not in {"accepted", "discarded"}:
            raise ValueError("Provisional intake status must be accepted or discarded.")
        intake = self.get(intake_id)
        if intake is None:
            raise ValueError(f"Provisional intake {intake_id} was not found.")
        if intake.status == status:
            return intake
        if intake.status != "pending":
            raise ValueError(f"Provisional intake {intake_id} was already {intake.status}.")
        decided_at = datetime.now(UTC)
        with sqlite3.connect(self._database_path) as connection:
            connection.execute(
                "UPDATE provisional_intakes SET status = ?, decided_at = ? WHERE id = ?",
                (status, decided_at.isoformat(), intake_id),
            )
        return replace(intake, status=status, decided_at=decided_at)

    def add_revision(self, intake_id: int, guidance: str, summary: str) -> None:
        """Record user-supplied routing context without changing canonical state."""
        with sqlite3.connect(self._database_path) as connection:
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute(
                "INSERT INTO provisional_intake_revisions (intake_id, guidance, summary, created_at) "
                "VALUES (?, ?, ?, ?)",
                (intake_id, guidance, summary, datetime.now(UTC).isoformat()),
            )

    @staticmethod
    def _from_row(row: tuple[object, ...]) -> ProvisionalIntake:
        return ProvisionalIntake(
            id=int(row[0]), event_id=str(row[1]), platform=str(row[2]), chat_id=str(row[3]),
            message_id=str(row[4]), kind=str(row[5]), staged_path=Path(str(row[6])),
            original_name=str(row[7]), summary=str(row[8]), status=str(row[9]),
            created_at=datetime.fromisoformat(str(row[10])),
            decided_at=datetime.fromisoformat(str(row[11])) if row[11] else None,
        )


class ProvisionalIntakeService:
    """Stage original material locally until the user decides whether to retain it."""

    def __init__(
        self,
        staging_dir: Path,
        repository: ProvisionalIntakeRepository,
        capture_service: InboxCaptureService,
        activity_service: ActivityService,
    ) -> None:
        self._staging_dir = staging_dir
        self._repository = repository
        self._capture = capture_service
        self._activity = activity_service

    def stage_file(self, event: IncomingEvent, original_path: Path) -> ProvisionalIntake:
        existing = self._repository.get_by_event_id(event.id)
        if existing is not None:
            return existing
        if not original_path.is_file():
            raise FileNotFoundError(original_path)
        original_name = event.attachments[0] if event.attachments else original_path.name
        staged_path = self._staging_path(event, original_name)
        self._staging_dir.mkdir(parents=True, exist_ok=True)
        copy2(original_path, staged_path)
        source_type = source_type_for_path(staged_path)
        type_label = source_type.value.replace("_", " ") if source_type else "unclassified file"
        intake = self._repository.add(
            ProvisionalIntake(
                None, event.id, event.platform, event.chat_id, event.message_id, "file",
                staged_path, original_name, f"This appears to be a {type_label}: {original_name}",
                "pending", datetime.now(UTC),
            )
        )
        self._activity.record(ActivityType.INTAKE_PROPOSED, object_id=str(intake.id), details=intake.summary)
        return intake

    def stage_text(self, event: IncomingEvent) -> ProvisionalIntake:
        """Stage a candidate note locally without placing it in the Inbox yet."""
        existing = self._repository.get_by_event_id(event.id)
        if existing is not None:
            return existing
        text = (event.text or "").strip()
        if not text:
            raise ValueError("A provisional text intake requires non-empty text.")
        self._staging_dir.mkdir(parents=True, exist_ok=True)
        staged_path = self._staging_path(event, "message.md")
        staged_path.write_text(text, encoding="utf-8")
        intake = self._repository.add(
            ProvisionalIntake(
                None, event.id, event.platform, event.chat_id, event.message_id, "text",
                staged_path, "message.md",
                f"This appears to be a {len(text)}-character text note.", "pending",
                datetime.now(UTC),
            )
        )
        self._activity.record(ActivityType.INTAKE_PROPOSED, object_id=str(intake.id), details=intake.summary)
        return intake

    def accept(self, intake_id: int, event: IncomingEvent) -> CaptureResult:
        intake = self._pending_for_chat(intake_id, event.chat_id)
        if not intake.staged_path.is_file():
            raise ValueError("The staged item is unavailable; send it again.")
        capture_event = IncomingEvent(
            id=intake.event_id, platform=intake.platform, chat_id=intake.chat_id,
            message_id=intake.message_id, reply_to_id=None, timestamp=event.timestamp,
            text=None, attachments=(intake.original_name,),
        )
        result = (
            self._capture.capture_file(capture_event, intake.staged_path)
            if intake.kind == "file"
            else self._capture.capture_text(
                replace(capture_event, text=intake.staged_path.read_text(encoding="utf-8"), attachments=())
            )
        )
        decided = self._repository.decide(intake_id, "accepted")
        decided.staged_path.unlink(missing_ok=True)
        self._activity.record(ActivityType.INTAKE_ACCEPTED, object_id=str(intake_id), details=str(result.source.path))
        return result

    def discard(self, intake_id: int, chat_id: str) -> ProvisionalIntake:
        intake = self._pending_for_chat(intake_id, chat_id)
        decided = self._repository.decide(intake_id, "discarded")
        decided.staged_path.unlink(missing_ok=True)
        self._activity.record(ActivityType.INTAKE_DISCARDED, object_id=str(intake_id), details=intake.original_name)
        return decided

    def add_context(self, intake_id: int, chat_id: str, guidance: str) -> ProvisionalIntake:
        """Attach explicit user context to a pending proposal and audit the revision."""
        intake = self._pending_for_chat(intake_id, chat_id)
        normalized = " ".join(guidance.split())
        if not normalized:
            raise ValueError("Tell me what this relates to after the provisional intake ID.")
        summary = f"{intake.summary} User context: {normalized}"
        self._repository.add_revision(intake_id, normalized, summary)
        self._activity.record(ActivityType.INTAKE_REVISED, object_id=str(intake_id), details=normalized)
        return replace(intake, summary=summary)

    def _pending_for_chat(self, intake_id: int, chat_id: str) -> ProvisionalIntake:
        intake = self._repository.get(intake_id)
        if intake is None:
            raise ValueError(f"Provisional intake {intake_id} was not found.")
        if intake.chat_id != chat_id:
            raise ValueError("This provisional intake belongs to another chat.")
        if intake.status != "pending":
            raise ValueError(f"Provisional intake {intake_id} was already {intake.status}.")
        return intake

    def _staging_path(self, event: IncomingEvent, original_name: str) -> Path:
        suffix = Path(original_name).suffix
        return self._staging_dir / f"{event.platform}-{event.chat_id}-{event.message_id}{suffix}"
