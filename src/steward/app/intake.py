"""Uploads from Telegram: staged, reviewed, then saved to the Inbox."""

from __future__ import annotations

from pathlib import Path

from steward.events import IncomingEvent
from steward.intake import ProvisionalIntake, ProvisionalIntakeService
from steward.presentation import PresentedReply, ReplyAction
from steward.reviews import ReviewContextRepository
from steward.roots import SourceRootRepository

INTAKE_COMMANDS = frozenset({"/intake_accept", "/intake_discard", "/intake_root", "/intake_context"})


class StewardIntakeApplication:
    """Save · Intended root · Add note · Discard for each staged file or note."""

    def __init__(
        self,
        service: ProvisionalIntakeService,
        *,
        contexts: ReviewContextRepository | None = None,
        roots: SourceRootRepository | None = None,
    ) -> None:
        self._service = service
        self._contexts = contexts
        self._roots = roots

    def begin_file(self, event: IncomingEvent, original_path: Path) -> PresentedReply:
        intake = self._service.stage_file(event, original_path)
        if intake.status != "pending":
            return PresentedReply(f"This file was already {'saved' if intake.status == 'accepted' else 'discarded'}.")
        return self._card(intake)

    def begin_note(self, event: IncomingEvent, text: str) -> PresentedReply | str:
        try:
            intake = self._service.stage_text(IncomingEvent(
                event.id, event.platform, event.chat_id, event.message_id, event.reply_to_id,
                event.timestamp, text,
            ))
        except ValueError as error:
            return str(error)
        return self._card(intake)

    def handle_command(self, event: IncomingEvent) -> str | PresentedReply | None:
        parts = (event.text or "").strip().split(maxsplit=2)
        if not parts or parts[0].partition("@")[0] not in INTAKE_COMMANDS:
            return None
        command = parts[0].partition("@")[0]
        if len(parts) < 2 or not parts[1].isdigit():
            return "Use the buttons on the upload card."
        intake_id = int(parts[1])
        argument = parts[2].strip() if len(parts) == 3 else ""
        try:
            if command == "/intake_accept":
                result = self._service.accept(intake_id, event)
                self._clear(event, intake_id)
                name = result.source.path.name
                return PresentedReply(
                    f"{'Already saved' if result.duplicate else 'Saved'} to the Inbox. It's listed in INBOX.md on your computer.",
                    (ReplyAction("Open", f"/source {result.source.id}"), ReplyAction("Inbox", "/inbox")),
                    title=name, icon="✅", reference=("source", result.source.id or 0),
                )
            if command == "/intake_discard":
                intake = self._service.discard(intake_id, event.chat_id)
                self._clear(event, intake_id)
                return PresentedReply(f"Discarded {intake.original_name}. Nothing was saved.", title="Discarded", icon="↩️")
            if command == "/intake_root":
                if not argument:
                    return self._root_picker(intake_id)
                root_id = None if argument.casefold() == "none" else int(argument) if argument.isdigit() else -1
                if root_id == -1:
                    return "Choose a folder from the list."
                return self._card(self._service.set_intended_root(intake_id, event.chat_id, root_id))
            # /intake_context: add a note now, or ask for it as the next message.
            if argument:
                return self._card(self._service.add_note(intake_id, event.chat_id, argument))
            if self._contexts is not None:
                self._contexts.set(event.platform, event.chat_id, "intake_context", intake_id)
            return PresentedReply(
                "Send the note to keep with this upload, e.g. which course or week it belongs to.",
                title="Add note", icon="💬", reference=("intake_context", intake_id),
            )
        except OSError:
            return "Local staging is unavailable right now. Try again shortly."
        except ValueError as error:
            return str(error)

    def note_followup(self, event: IncomingEvent) -> PresentedReply | str | None:
        """Treat the next message after Add note, or a reply to an upload card, as its note."""
        text = (event.text or "").strip()
        if self._contexts is None or not text or text.startswith("/"):
            return None
        context = self._contexts.get(event.platform, event.chat_id)
        if context is None or not isinstance(context.identifier, int):
            return None
        is_note_prompt = context.kind == "intake_context"
        replies_to_card = context.kind == "intake" and event.reply_to_id is not None
        if not (is_note_prompt or replies_to_card):
            return None
        try:
            intake = self._service.add_note(context.identifier, event.chat_id, text)
        except ValueError as error:
            return str(error)
        self._contexts.set(event.platform, event.chat_id, "intake", context.identifier)
        return self._card(intake)

    def _card(self, intake: ProvisionalIntake) -> PresentedReply:
        lines = ["Not saved yet."]
        if intake.intended_root_id is not None and self._roots is not None:
            root = next((item.name for item in self._roots.list_all() if item.id == intake.intended_root_id), None)
            if root:
                lines.append(f"Intended root: {root}")
        note = self._service.note_for(intake.id or 0)
        if note:
            lines.append(f"Note: {note}")
        return PresentedReply(
            "\n".join(lines),
            (
                ReplyAction("Save", f"/intake_accept {intake.id}"),
                ReplyAction("Intended root", f"/intake_root {intake.id}"),
                ReplyAction("Add note", f"/intake_context {intake.id}"),
                ReplyAction("Discard", f"/intake_discard {intake.id}"),
            ),
            title=intake.original_name if intake.kind == "file" else "Note",
            icon="📥",
            reference=("intake", intake.id or 0),
        )

    def _root_picker(self, intake_id: int) -> PresentedReply | str:
        roots = self._roots.list_all() if self._roots is not None else []
        if not roots:
            return "You have no folders yet. Save without one; Codex can still file it from INBOX.md."
        return PresentedReply(
            "Where should this be filed? The file stays in the Inbox until it's moved.",
            tuple(ReplyAction(root.name[:48], f"/intake_root {intake_id} {root.id}") for root in roots)
            + (ReplyAction("None", f"/intake_root {intake_id} none"),),
            title="Intended root", icon="📁", reference=("intake", intake_id),
        )

    def _clear(self, event: IncomingEvent, intake_id: int) -> None:
        if self._contexts is None:
            return
        current = self._contexts.get(event.platform, event.chat_id)
        if current is not None and current.identifier == intake_id and current.kind in {"intake", "intake_context"}:
            self._contexts.clear(event.platform, event.chat_id)
