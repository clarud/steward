"""Reviewable temporary intake before material becomes a durable Source."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from shutil import copy2
from typing import TYPE_CHECKING

from steward.activity import ActivityService, ActivityType
from steward.capture import CaptureResult, InboxCaptureService
from steward.events import IncomingEvent
from steward.sources import source_type_for_path
from steward.privacy import PrivacyRule, PrivacyService
from steward.sources.inbox_context import SourceInboxContext, SourceInboxContextRepository

if TYPE_CHECKING:
    from steward.roots import SourceRootRepository


class IntakeAnalysisMode(StrEnum):
    """The model boundary a user chooses before a staged item is retained."""

    EXTERNAL = "external"
    LOCAL = "local"
    NONE = "none"

    @property
    def privacy_rule(self) -> PrivacyRule:
        return {
            IntakeAnalysisMode.EXTERNAL: PrivacyRule.EXTERNAL_ALLOWED,
            IntakeAnalysisMode.LOCAL: PrivacyRule.LOCAL_MODEL_ONLY,
            IntakeAnalysisMode.NONE: PrivacyRule.NO_MODEL,
        }[self]


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
    category: str
    summary: str
    analysis_mode: IntakeAnalysisMode
    status: str
    created_at: datetime
    decided_at: datetime | None = None
    diagnostic: str = ""
    intended_root_id: int | None = None


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
                    original_name, category, summary, analysis_mode, status, created_at, decided_at, diagnostic, intended_root_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    intake.event_id, intake.platform, intake.chat_id, intake.message_id,
                    intake.kind, str(intake.staged_path), intake.original_name, intake.category,
                    intake.summary, intake.analysis_mode.value, intake.status, intake.created_at.isoformat(),
                    intake.decided_at.isoformat() if intake.decided_at else None,
                    intake.diagnostic, intake.intended_root_id,
                ),
            )
        return replace(intake, id=int(cursor.lastrowid))

    def get(self, intake_id: int) -> ProvisionalIntake | None:
        with sqlite3.connect(self._database_path) as connection:
            row = connection.execute(
                """
                SELECT id, event_id, platform, chat_id, message_id, kind, staged_path,
                       original_name, category, summary, analysis_mode, status, created_at, decided_at, diagnostic, intended_root_id
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
                       original_name, category, summary, analysis_mode, status, created_at, decided_at, diagnostic, intended_root_id
                FROM provisional_intakes WHERE event_id = ?
                """,
                (event_id,),
            ).fetchone()
        return self._from_row(row) if row is not None else None

    def get_pending_for_message(
        self, *, platform: str, chat_id: str, message_id: str
    ) -> ProvisionalIntake | None:
        """Find the still-reviewable intake represented by one chat message.

        Telegram reply IDs are message IDs, not Steward intake IDs. Looking up
        the pending item here keeps that transport detail out of capture and
        prevents a reply in another chat from selecting an item.
        """

        with sqlite3.connect(self._database_path) as connection:
            row = connection.execute(
                """
                SELECT id, event_id, platform, chat_id, message_id, kind, staged_path,
                       original_name, category, summary, analysis_mode, status, created_at, decided_at, diagnostic, intended_root_id
                FROM provisional_intakes
                WHERE platform = ? AND chat_id = ? AND message_id = ? AND status = 'pending'
                """,
                (platform, chat_id, message_id),
            ).fetchone()
        return self._from_row(row) if row is not None else None

    def list_all(self) -> tuple[ProvisionalIntake, ...]:
        """List staged decisions for a presentation layer; callers still scope by chat."""
        with sqlite3.connect(self._database_path) as connection:
            rows = connection.execute(
                """
                SELECT id, event_id, platform, chat_id, message_id, kind, staged_path,
                       original_name, category, summary, analysis_mode, status, created_at, decided_at, diagnostic, intended_root_id
                FROM provisional_intakes ORDER BY id
                """
            ).fetchall()
        return tuple(self._from_row(row) for row in rows)

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

    def latest_guidance(self, intake_id: int) -> str | None:
        """Return the latest user routing guidance for one staged item.

        Revisions remain an audit trail. This accessor intentionally returns
        only the most recent guidance because it is the user's current
        instruction for the next proposal, not a replacement for the source.
        """

        with sqlite3.connect(self._database_path) as connection:
            row = connection.execute(
                "SELECT guidance FROM provisional_intake_revisions "
                "WHERE intake_id = ? ORDER BY id DESC LIMIT 1",
                (intake_id,),
            ).fetchone()
        return str(row[0]) if row is not None else None

    def set_classification(self, intake_id: int, category: str, summary: str) -> None:
        with sqlite3.connect(self._database_path) as connection:
            connection.execute(
                "UPDATE provisional_intakes SET category = ?, summary = ? WHERE id = ?",
                (category, summary, intake_id),
            )

    def set_analysis_mode(self, intake_id: int, mode: IntakeAnalysisMode) -> ProvisionalIntake:
        intake = self.get(intake_id)
        if intake is None:
            raise ValueError(f"Provisional intake {intake_id} was not found.")
        if intake.status != "pending":
            raise ValueError(f"Provisional intake {intake_id} was already {intake.status}.")
        if intake.analysis_mode is mode:
            return intake
        with sqlite3.connect(self._database_path) as connection:
            connection.execute(
                "UPDATE provisional_intakes SET analysis_mode = ? WHERE id = ?",
                (mode.value, intake_id),
            )
        return replace(intake, analysis_mode=mode)

    def set_intended_root(self, intake_id: int, root_id: int | None) -> ProvisionalIntake:
        intake = self.get(intake_id)
        if intake is None:
            raise ValueError(f"Provisional intake {intake_id} was not found.")
        if intake.status != "pending":
            raise ValueError(f"Provisional intake {intake_id} was already {intake.status}.")
        with sqlite3.connect(self._database_path) as connection:
            connection.execute(
                "UPDATE provisional_intakes SET intended_root_id = ? WHERE id = ?", (root_id, intake_id)
            )
        return replace(intake, intended_root_id=root_id)

    @staticmethod
    def _from_row(row: tuple[object, ...]) -> ProvisionalIntake:
        return ProvisionalIntake(
            id=int(row[0]), event_id=str(row[1]), platform=str(row[2]), chat_id=str(row[3]),
            message_id=str(row[4]), kind=str(row[5]), staged_path=Path(str(row[6])),
            original_name=str(row[7]), category=str(row[8]), summary=str(row[9]),
            analysis_mode=IntakeAnalysisMode(str(row[10])), status=str(row[11]),
            created_at=datetime.fromisoformat(str(row[12])),
            decided_at=datetime.fromisoformat(str(row[13])) if row[13] else None,
            diagnostic=str(row[14]), intended_root_id=int(row[15]) if row[15] is not None else None,
        )


class ProvisionalIntakeService:
    """Stage original material locally until the user decides whether to retain it."""

    def __init__(
        self,
        staging_dir: Path,
        repository: ProvisionalIntakeRepository,
        capture_service: InboxCaptureService,
        activity_service: ActivityService,
        privacy_service: PrivacyService,
        roots: "SourceRootRepository | None" = None,
        inbox_contexts: SourceInboxContextRepository | None = None,
    ) -> None:
        self._staging_dir = staging_dir
        self._repository = repository
        self._capture = capture_service
        self._activity = activity_service
        self._privacy = privacy_service
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
        category, summary = self._classify_file(staged_path, original_name)
        intake = self._repository.add(
            ProvisionalIntake(
                None, event.id, event.platform, event.chat_id, event.message_id, "file",
                staged_path, original_name, category, summary,
                IntakeAnalysisMode.NONE, "pending", datetime.now(UTC),
                diagnostic="Staging used only the filename and supported file type. The file was not extracted or sent to a model yet.",
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
                staged_path, "message.md", *self._classify_text(text),
                IntakeAnalysisMode.NONE, "pending", datetime.now(UTC),
                diagnostic="Staging used the message text locally. No parser or model was used yet.",
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
        if result.source.id is None:
            raise RuntimeError("Captured sources must have an ID before applying intake privacy.")
        self._privacy.set_rule(result.source.id, intake.analysis_mode.privacy_rule)
        if self._inbox_contexts is not None:
            root = next(
                (item for item in self._roots.list_all() if item.id == intake.intended_root_id), None
            ) if self._roots is not None and intake.intended_root_id is not None else None
            self._inbox_contexts.set(SourceInboxContext(
                result.source.id,
                root.id if root is not None else None,
                root.name if root is not None else None,
                self._repository.latest_guidance(intake_id),
                intake.platform,
                datetime.now(UTC),
            ))
        decided = self._repository.decide(intake_id, "accepted")
        decided.staged_path.unlink(missing_ok=True)
        self._activity.record(
            ActivityType.INTAKE_ACCEPTED,
            object_id=str(intake_id),
            details=f"{result.source.path.name}; analysis={intake.analysis_mode.value}",
        )
        return result

    def get(self, intake_id: int) -> ProvisionalIntake | None:
        """Read one staged intake for a transport-level next-step decision."""

        return self._repository.get(intake_id)

    def pending_for_reply(self, event: IncomingEvent) -> ProvisionalIntake | None:
        """Return the pending intake explicitly targeted by a chat reply."""

        if event.reply_to_id is None:
            return None
        return self._repository.get_pending_for_message(
            platform=event.platform,
            chat_id=event.chat_id,
            message_id=event.reply_to_id,
        )

    def guidance_for(self, intake_id: int) -> str | None:
        """Read the latest explicit guidance without changing intake state."""

        return self._repository.latest_guidance(intake_id)

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
        category, base_summary = self._classify_text(normalized)
        category = category if category != "knowledge" or intake.category == "uncertain" else intake.category
        summary = f"{base_summary} Original assessment: {intake.summary} User context: {normalized}"
        self._repository.add_revision(intake_id, normalized, summary)
        self._repository.set_classification(intake_id, category, summary)
        self._activity.record(ActivityType.INTAKE_REVISED, object_id=str(intake_id), details=normalized)
        return replace(intake, category=category, summary=summary)

    def set_analysis_mode(
        self, intake_id: int, chat_id: str, mode: IntakeAnalysisMode
    ) -> ProvisionalIntake:
        """Persist a user's model-boundary choice before capture can invoke organization."""
        self._pending_for_chat(intake_id, chat_id)
        intake = self._repository.set_analysis_mode(intake_id, mode)
        self._activity.record(
            ActivityType.INTAKE_ANALYSIS_SELECTED,
            object_id=str(intake_id),
            details=f"analysis={mode.value}",
        )
        return intake

    def set_intended_root(self, intake_id: int, chat_id: str, root_id: int | None) -> ProvisionalIntake:
        """Attach an optional authorized-root intent without moving the staged file."""
        self._pending_for_chat(intake_id, chat_id)
        root_name = None
        if root_id is not None:
            if self._roots is None:
                raise ValueError("Authorized roots are unavailable on this Steward process.")
            root = next((item for item in self._roots.list_all() if item.id == root_id and item.enabled), None)
            if root is None:
                raise ValueError("Choose an enabled authorized root from the picker.")
            root_name = root.name
        intake = self._repository.set_intended_root(intake_id, root_id)
        details = f"intended_root={root_name}" if root_name is not None else "intended_root=none"
        self._activity.record(ActivityType.INTAKE_REVISED, object_id=str(intake_id), details=details)
        return intake

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

    @staticmethod
    def _classify_file(path: Path, original_name: str) -> tuple[str, str]:
        name = original_name.casefold()
        if any(term in name for term in (
            "flight", "itinerary", "booking", "reservation", "hotel", "boarding pass", "ticket",
            "passport", "visa", "receipt", "invoice", "warranty", "contract", "certificate",
            "subscription",
        )):
            return (
                "record",
                f"Likely record candidate: {original_name}. After you save it, Steward will propose only a supported, evidence-backed record when enough fields are available. No content was sent to a model.",
            )
        source_type = source_type_for_path(path)
        label = source_type.value.replace("_", " ") if source_type else "unclassified file"
        return "document", f"Likely document ({label}): {original_name}. No content was sent to a model."

    @staticmethod
    def _classify_text(text: str) -> tuple[str, str]:
        normalized = text.casefold()
        if normalized.startswith(("https://", "http://")):
            return "reference", "Likely shared link or reference. No content was sent to a model."
        if any(token in normalized for token in ("deadline", "todo", "task", "remind me")):
            return "task", "Likely task or deadline. No content was sent to a model."
        if any(token in normalized for token in (
            "flight", "itinerary", "booking", "reservation", "hotel", "boarding pass", "ticket",
            "passport", "visa", "receipt", "invoice", "warranty", "contract", "certificate",
            "subscription",
        )):
            return (
                "record",
                "Likely life-record candidate. After you save it, Steward will propose only a supported, evidence-backed record when enough fields are available. No content was sent to a model.",
            )
        if normalized.startswith(("thought:", "note:")):
            return "thought", "Likely personal thought or note. No content was sent to a model."
        return "knowledge", "Likely knowledge or reference note. No content was sent to a model."
