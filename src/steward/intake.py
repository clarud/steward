"""Staged uploads: nothing enters the Inbox until the owner chooses Save."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from shutil import copy2
from typing import TYPE_CHECKING

from steward.activity import ActivityService, ActivityType
from steward.capture import CaptureResult, InboxCaptureService
from steward.events import IncomingEvent
from steward.sources.inbox_context import SourceInboxContext, SourceInboxContextRepository

if TYPE_CHECKING:
    from steward.roots import SourceRootRepository

_COLUMNS = (
    "id, event_id, platform, chat_id, message_id, kind, staged_path, original_name, "
    "status, created_at, decided_at, intended_root_id"
)


@dataclass(frozen=True, slots=True)
class ProvisionalIntake:
    """A locally staged file or note awaiting Save or Discard."""

    id: int | None
    event_id: str
    platform: str
    chat_id: str
    message_id: str
    kind: str
    staged_path: Path
    original_name: str
    status: str
    created_at: datetime
    decided_at: datetime | None = None
    intended_root_id: int | None = None


class ProvisionalIntakeRepository:
    """Durably track staged items so a restart cannot lose a pending decision."""

    def __init__(self, database_path: Path) -> None:
        self._database_path = database_path

    def add(self, intake: ProvisionalIntake) -> ProvisionalIntake:
        if intake.id is not None:
            raise ValueError("Only an unstored provisional intake can be added.")
        with sqlite3.connect(self._database_path) as connection:
            # category, summary, analysis_mode and diagnostic are retired
            # columns kept for schema compatibility.
            cursor = connection.execute(
                """
                INSERT INTO provisional_intakes (
                    event_id, platform, chat_id, message_id, kind, staged_path, original_name,
                    category, summary, analysis_mode, status, created_at, decided_at, diagnostic,
                    intended_root_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, '', '', 'external', ?, ?, ?, '', ?)
                """,
                (
                    intake.event_id, intake.platform, intake.chat_id, intake.message_id,
                    intake.kind, str(intake.staged_path), intake.original_name, intake.status,
                    intake.created_at.isoformat(),
                    intake.decided_at.isoformat() if intake.decided_at else None,
                    intake.intended_root_id,
                ),
            )
        return replace(intake, id=int(cursor.lastrowid))

    def get(self, intake_id: int) -> ProvisionalIntake | None:
        return self._one(f"SELECT {_COLUMNS} FROM provisional_intakes WHERE id = ?", (intake_id,))

    def get_by_event_id(self, event_id: str) -> ProvisionalIntake | None:
        return self._one(f"SELECT {_COLUMNS} FROM provisional_intakes WHERE event_id = ?", (event_id,))

    def decide(self, intake_id: int, status: str) -> ProvisionalIntake:
        if status not in {"accepted", "discarded"}:
            raise ValueError("Provisional intake status must be accepted or discarded.")
        intake = self.get(intake_id)
        if intake is None:
            raise ValueError(f"Staged item {intake_id} was not found.")
        if intake.status == status:
            return intake
        if intake.status != "pending":
            raise ValueError(f"Staged item {intake_id} was already {intake.status}.")
        decided_at = datetime.now(UTC)
        with sqlite3.connect(self._database_path) as connection:
            connection.execute(
                "UPDATE provisional_intakes SET status = ?, decided_at = ? WHERE id = ?",
                (status, decided_at.isoformat(), intake_id),
            )
        return replace(intake, status=status, decided_at=decided_at)

    def add_note(self, intake_id: int, note: str) -> None:
        """Record the owner's note; the latest one is used when the item is saved."""
        with sqlite3.connect(self._database_path) as connection:
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute(
                "INSERT INTO provisional_intake_revisions (intake_id, guidance, summary, created_at) "
                "VALUES (?, ?, '', ?)",
                (intake_id, note, datetime.now(UTC).isoformat()),
            )

    def latest_note(self, intake_id: int) -> str | None:
        with sqlite3.connect(self._database_path) as connection:
            row = connection.execute(
                "SELECT guidance FROM provisional_intake_revisions WHERE intake_id = ? ORDER BY id DESC LIMIT 1",
                (intake_id,),
            ).fetchone()
        return str(row[0]) if row is not None else None

    def set_intended_root(self, intake_id: int, root_id: int | None) -> ProvisionalIntake:
        intake = self.get(intake_id)
        if intake is None:
            raise ValueError(f"Staged item {intake_id} was not found.")
        if intake.status != "pending":
            raise ValueError(f"Staged item {intake_id} was already {intake.status}.")
        with sqlite3.connect(self._database_path) as connection:
            connection.execute(
                "UPDATE provisional_intakes SET intended_root_id = ? WHERE id = ?", (root_id, intake_id)
            )
        return replace(intake, intended_root_id=root_id)

    def _one(self, sql: str, parameters: tuple[object, ...]) -> ProvisionalIntake | None:
        with sqlite3.connect(self._database_path) as connection:
            row = connection.execute(sql, parameters).fetchone()
        if row is None:
            return None
        return ProvisionalIntake(
            id=int(row[0]), event_id=str(row[1]), platform=str(row[2]), chat_id=str(row[3]),
            message_id=str(row[4]), kind=str(row[5]), staged_path=Path(str(row[6])),
            original_name=str(row[7]), status=str(row[8]),
            created_at=datetime.fromisoformat(str(row[9])),
            decided_at=datetime.fromisoformat(str(row[10])) if row[10] else None,
            intended_root_id=int(row[11]) if row[11] is not None else None,
        )


class ProvisionalIntakeService:
    """Stage uploads locally until the owner saves or discards them."""

    def __init__(
        self,
        staging_dir: Path,
        repository: ProvisionalIntakeRepository,
        capture_service: InboxCaptureService,
        activity_service: ActivityService,
        roots: "SourceRootRepository | None" = None,
        inbox_contexts: SourceInboxContextRepository | None = None,
    ) -> None:
        self._staging_dir = staging_dir
        self._repository = repository
        self._capture = capture_service
        self._activity = activity_service
        self._roots = roots
        self._inbox_contexts = inbox_contexts

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
        return self._stage(event, "file", staged_path, original_name)

    def stage_text(self, event: IncomingEvent) -> ProvisionalIntake:
        existing = self._repository.get_by_event_id(event.id)
        if existing is not None:
            return existing
        text = (event.text or "").strip()
        if not text:
            raise ValueError("A note needs some text.")
        self._staging_dir.mkdir(parents=True, exist_ok=True)
        staged_path = self._staging_path(event, "note.md")
        staged_path.write_text(text, encoding="utf-8")
        return self._stage(event, "text", staged_path, "note.md")

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
        if result.source.id is None:
            raise RuntimeError("Captured sources must have an ID.")
        if self._inbox_contexts is not None:
            root = next(
                (item for item in self._roots.list_all() if item.id == intake.intended_root_id), None
            ) if self._roots is not None and intake.intended_root_id is not None else None
            self._inbox_contexts.set(SourceInboxContext(
                result.source.id,
                root.id if root is not None else None,
                root.name if root is not None else None,
                self._repository.latest_note(intake_id),
                intake.platform,
                datetime.now(UTC),
            ))
            self._capture.refresh_queue()
        decided = self._repository.decide(intake_id, "accepted")
        decided.staged_path.unlink(missing_ok=True)
        self._activity.record(ActivityType.INTAKE_ACCEPTED, object_id=str(intake_id), details=result.source.path.name)
        return result

    def get(self, intake_id: int) -> ProvisionalIntake | None:
        return self._repository.get(intake_id)

    def note_for(self, intake_id: int) -> str | None:
        return self._repository.latest_note(intake_id)

    def discard(self, intake_id: int, chat_id: str) -> ProvisionalIntake:
        intake = self._pending_for_chat(intake_id, chat_id)
        decided = self._repository.decide(intake_id, "discarded")
        decided.staged_path.unlink(missing_ok=True)
        self._activity.record(ActivityType.INTAKE_DISCARDED, object_id=str(intake_id), details=intake.original_name)
        return decided

    def add_note(self, intake_id: int, chat_id: str, note: str) -> ProvisionalIntake:
        intake = self._pending_for_chat(intake_id, chat_id)
        normalized = " ".join(note.split())
        if not normalized:
            raise ValueError("The note is empty.")
        self._repository.add_note(intake_id, normalized)
        self._activity.record(ActivityType.INTAKE_REVISED, object_id=str(intake_id), details=normalized)
        return intake

    def set_intended_root(self, intake_id: int, chat_id: str, root_id: int | None) -> ProvisionalIntake:
        """Record where the owner wants the file filed; the file stays in the Inbox."""
        self._pending_for_chat(intake_id, chat_id)
        root_name = None
        if root_id is not None:
            root = next(
                (item for item in self._roots.list_all() if item.id == root_id), None
            ) if self._roots is not None else None
            if root is None:
                raise ValueError("Choose a root from the list.")
            root_name = root.name
        intake = self._repository.set_intended_root(intake_id, root_id)
        self._activity.record(
            ActivityType.INTAKE_REVISED, object_id=str(intake_id), details=f"intended_root={root_name or 'none'}",
        )
        return intake

    def _stage(self, event: IncomingEvent, kind: str, staged_path: Path, original_name: str) -> ProvisionalIntake:
        intake = self._repository.add(ProvisionalIntake(
            None, event.id, event.platform, event.chat_id, event.message_id, kind,
            staged_path, original_name, "pending", datetime.now(UTC),
        ))
        self._activity.record(ActivityType.INTAKE_PROPOSED, object_id=str(intake.id), details=original_name)
        return intake

    def _pending_for_chat(self, intake_id: int, chat_id: str) -> ProvisionalIntake:
        intake = self._repository.get(intake_id)
        if intake is None:
            raise ValueError(f"Staged item {intake_id} was not found.")
        if intake.chat_id != chat_id:
            raise ValueError("This staged item belongs to another chat.")
        if intake.status != "pending":
            raise ValueError(f"Staged item {intake_id} was already {intake.status}.")
        return intake

    def _staging_path(self, event: IncomingEvent, original_name: str) -> Path:
        suffix = Path(original_name).suffix
        return self._staging_dir / f"{event.platform}-{event.chat_id}-{event.message_id}{suffix}"
