"""Getting files in: Inbox capture, staged intake review, and Drive/Gmail imports."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Protocol

from steward.capture import CaptureResult, InboxCaptureService
from steward.events import IncomingEvent
from steward.intake import IntakeAnalysisMode, ProvisionalIntake, ProvisionalIntakeService
from steward.presentation import PresentedReply, ReplyAction
from steward.reviews import ReviewContextRepository
from steward.roots import SourceRootRepository


def _external_import_failure(operation: str, retry_command: str) -> PresentedReply:
    return PresentedReply(
        f"{operation} could not finish. The service may be unreachable or need local reauthorization.\n\n"
        "Complete any required Google sign-in on the Steward computer, then retry. "
        "Do not send tokens or client-secret files here. A failed reply does not prove that an import saved nothing; check Inbox before retrying.",
        (ReplyAction("Retry", retry_command), ReplyAction("Inbox", "/inbox")),
        title="Import needs attention", icon="⚠️",
    )


class StewardCaptureApplication:
    """Explicitly preserve text supplied with Telegram's /save command."""

    def __init__(self, capture_service: InboxCaptureService) -> None:
        self._capture_service = capture_service

    def handle(self, event: IncomingEvent) -> str:
        try:
            return self.format_result(self.capture(event))
        except ValueError as error:
            return str(error)

    def capture(self, event: IncomingEvent) -> CaptureResult:
        """Persist text and expose its result to an optional follow-on workflow."""

        text = (event.text or "").partition(" ")[2].strip()
        if not text:
            raise ValueError("Use /save followed by the text you want Steward to keep.")
        return self._capture_service.capture_text(
            IncomingEvent(
                id=event.id, platform=event.platform, chat_id=event.chat_id,
                message_id=event.message_id, reply_to_id=event.reply_to_id,
                timestamp=event.timestamp, text=text, attachments=event.attachments,
            )
        )

    @staticmethod
    def format_result(result: CaptureResult) -> str:
        if result.duplicate:
            return f"Already saved to Inbox: {result.source.path.name}"
        return f"Saved to Inbox: {result.source.path.name}"

    def handle_file(self, event: IncomingEvent, original_path: Path) -> str:
        """Preserve a document already downloaded by a transport adapter."""

        return self.format_result(self.capture_file(event, original_path))

    def capture_file(self, event: IncomingEvent, original_path: Path) -> CaptureResult:
        """Persist a downloaded document and expose its result to follow-on workflows."""

        return self._capture_service.capture_file(event, original_path)


class StewardProvisionalIntakeApplication:
    """Present staged attachment intake as a reviewable save-or-discard choice."""

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
        if intake.status == "accepted":
            return PresentedReply("This attachment was already saved.")
        if intake.status == "discarded":
            return PresentedReply("This attachment was previously discarded. Send it again to reconsider it.")
        return self._review_card(intake)

    def begin_text(self, event: IncomingEvent) -> PresentedReply:
        intake = self._service.stage_text(event)
        return self._review_card(intake)

    @staticmethod
    def should_propose_text(event: IncomingEvent) -> bool:
        """Stage likely personal material, while keeping ordinary conversation ephemeral.

        Staging is deliberately reversible: recognizing a flight, task, or note
        never saves it.  Questions are resolved earlier by ``IntentResolver``.
        """
        text = (event.text or "").strip()
        normalized = text.casefold()
        explicit_prefixes = (
            "note:", "thought:", "remember:", "deadline:", "todo:", "task:",
            "note ", "thought ", "remember ", "todo ", "task ",
        )
        personal_signals = (
            "flight", "itinerary", "booking", "reservation", "hotel", "boarding pass", "ticket",
            "passport", "visa", "appointment", "contract", "certificate", "subscription",
            "receipt", "invoice", "warranty",
            "deadline", "due ", "submit ", "remind me", "to do", "todo", "task",
        )
        return (
            len(text) >= 280
            or text.count("\n") >= 2
            or normalized.startswith(explicit_prefixes)
            or normalized.startswith(("https://", "http://"))
            or (len(text) >= 24 and any(signal in normalized for signal in personal_signals))
        )

    def handle_command(self, event: IncomingEvent) -> CaptureResult | str | PresentedReply | None:
        command, separator, argument = (event.text or "").strip().partition(" ")
        command = command.partition("@")[0]
        if command not in {"/intake_accept", "/intake_discard", "/intake_context", "/intake_analysis", "/intake_root"}:
            return None
        intake_identifier, context_separator, context = argument.strip().partition(" ")
        if not separator or not intake_identifier.isdigit():
            return f"Use {command} followed by a numeric provisional intake ID."
        intake_id = int(intake_identifier)
        try:
            if command == "/intake_accept":
                return self._service.accept(intake_id, event)
            if command == "/intake_context":
                if not context_separator:
                    if self._contexts is not None:
                        self._contexts.set(event.platform, event.chat_id, "intake_context", intake_id)
                    return PresentedReply(
                        "Tell me what this relates to, such as a course, project, or purpose. "
                        "I will update this pending review; nothing will be saved yet.",
                        title="Add context",
                        icon="💬",
                        reference=("intake_context", intake_id),
                    )
                intake = self._service.add_context(intake_id, event.chat_id, context)
                return self._review_card(intake)
            if command == "/intake_analysis":
                if not context_separator:
                    return "Use /intake_analysis followed by an intake ID and external, local, or none."
                try:
                    mode = IntakeAnalysisMode(context.casefold())
                except ValueError:
                    return "Intake analysis mode must be external, local, or none."
                intake = self._service.set_analysis_mode(intake_id, event.chat_id, mode)
                description = {
                    IntakeAnalysisMode.EXTERNAL: "A configured external model may analyze extracted content after you save it.",
                    IntakeAnalysisMode.LOCAL: "Only a configured local model may analyze extracted content after you save it.",
                    IntakeAnalysisMode.NONE: "No model may analyze this item after you save it.",
                }[mode]
                return self._review_card(intake, analysis_description=description)
            if command == "/intake_root":
                if not context_separator:
                    return self._root_picker(intake_id, event.chat_id)
                if context.casefold() in {"none", "clear"}:
                    intake = self._service.set_intended_root(intake_id, event.chat_id, None)
                elif context.isdigit():
                    intake = self._service.set_intended_root(intake_id, event.chat_id, int(context))
                else:
                    return "Choose an intended root from the picker, or use `none` to clear it."
                return self._review_card(intake)
            intake = self._service.discard(intake_id, event.chat_id)
        except OSError:
            return "Could not update this provisional intake because local staging is temporarily unavailable. Try again later."
        except ValueError as error:
            return str(error)
        if self._contexts is not None:
            current = self._contexts.get(event.platform, event.chat_id)
            if current is not None and current.identifier == intake.id and current.kind in {"intake", "intake_context"}:
                self._contexts.clear(event.platform, event.chat_id)
        return PresentedReply(
            f"Discarded {intake.original_name}. Its staged local copy was removed and nothing was saved to Inbox.",
            (ReplyAction("Inbox", "/inbox"), ReplyAction("Home", "/home")),
            title="Staged item discarded",
            icon="↩️",
        )

    def pending_category_for_acceptance(self, event: IncomingEvent) -> tuple[str, str, str | None] | None:
        """Return pending classification and routing guidance before acceptance.

        The method is intentionally read-only.  It lets the application select
        a next review card after capture without trusting callback text. The
        explicit guidance is retained separately from the mutable summary, so
        it can influence a proposal after the original enters Inbox.
        """

        command, separator, argument = (event.text or "").strip().partition(" ")
        if command.partition("@")[0] != "/intake_accept" or not separator or not argument.strip().isdigit():
            return None
        intake = self._service.get(int(argument.strip()))
        if intake is None or intake.status != "pending" or intake.chat_id != event.chat_id:
            return None
        return intake.category, intake.original_name, self._service.guidance_for(intake.id or 0)

    def reply_save_event(self, event: IncomingEvent) -> IncomingEvent | None:
        """Translate a reply-only ``/save`` into the matching intake decision.

        The original attachment was already downloaded into the local staged
        intake. We therefore do not re-download Telegram media or infer a
        filename from reply text; this only accepts the exact pending item the
        user replied to.
        """

        raw = (event.text or "").strip()
        command = raw.partition(" ")[0].partition("@")[0]
        if command != "/save" or raw != raw.partition(" ")[0]:
            return None
        intake = self._service.pending_for_reply(event)
        if intake is None or intake.id is None:
            return None
        return replace(event, text=f"/intake_accept {intake.id}")

    def handle_followup(self, event: IncomingEvent) -> PresentedReply | str | None:
        """Attach the next ordinary message to an explicitly requested intake context."""

        if self._contexts is None or not (event.text or "").strip() or (event.text or "").startswith("/"):
            return None
        # An explicit capture prefix is stronger evidence of a new item than a
        # previously displayed "Add context" prompt. A user can therefore
        # stage another note before replying to an older prompt.
        if self.should_propose_text(event):
            return None
        context = self._contexts.get(event.platform, event.chat_id)
        if context is None or context.kind != "intake_context":
            return None
        try:
            intake = self._service.add_context(context.identifier, event.chat_id, (event.text or "").strip())
        except OSError:
            return "Could not update this pending review because local staging is temporarily unavailable. Try again later."
        except ValueError as error:
            return str(error)
        self._contexts.set(event.platform, event.chat_id, "intake", intake.id or context.identifier)
        return self._review_card(intake)

    def _review_card(
        self,
        intake: ProvisionalIntake,
        *,
        analysis_description: str | None = None,
    ) -> PresentedReply:
        description = analysis_description or {
            IntakeAnalysisMode.EXTERNAL: "A configured external model may analyze extracted content after you save it.",
            IntakeAnalysisMode.LOCAL: "Only a configured local model may analyze extracted content after you save it.",
            IntakeAnalysisMode.NONE: "No model will analyze this item after you save it.",
        }[intake.analysis_mode]
        intended_root = None
        if intake.intended_root_id is not None and self._roots is not None:
            intended_root = next((root.name for root in self._roots.list_all() if root.id == intake.intended_root_id), None)
        intent_line = f"\nIntended root: {intended_root}" if intended_root else ""
        return PresentedReply(
            f"Type: {intake.category}\nSummary: {intake.summary}\n\n"
            f"Assessment: {intake.diagnostic}\n\n"
            f"{description}{intent_line}\n\nIt is staged locally and has not been saved.",
            self._actions(intake),
            title=f"Review {intake.original_name}",
            icon="📄",
            reference=("intake", intake.id or 0),
        )

    def _root_picker(self, intake_id: int, chat_id: str) -> PresentedReply | str:
        if self._roots is None:
            return "Authorized roots are unavailable on this Steward process."
        intake = self._service.get(intake_id)
        if intake is None or intake.status != "pending" or intake.chat_id != chat_id:
            return "That staged item is no longer available in this chat."
        roots = [root for root in self._roots.list_all() if root.enabled]
        if not roots:
            return "No enabled authorized roots are available. Add a root locally first, or keep this item unassigned in Inbox."
        return PresentedReply(
            "Choose an optional intended root. This only records your routing context; the file stays in Inbox.",
            tuple(ReplyAction(root.name[:48], f"/intake_root {intake_id} {root.id}") for root in roots)
            + (ReplyAction("No intended root", f"/intake_root {intake_id} none"),),
            title="Intended root",
            icon="📁",
            reference=("intake", intake_id),
        )

    @staticmethod
    def _actions(intake: ProvisionalIntake) -> tuple[ReplyAction, ...]:
        """Keep the model-boundary choice visible before a save can trigger organization."""
        save_label = {
            IntakeAnalysisMode.EXTERNAL: "Save (external allowed)",
            IntakeAnalysisMode.LOCAL: "Save (local only)",
            IntakeAnalysisMode.NONE: "Save (no model)",
        }[intake.analysis_mode]
        return (
            ReplyAction(save_label, f"/intake_accept {intake.id}"),
            ReplyAction("Use local model", f"/intake_analysis {intake.id} local"),
            ReplyAction("Allow external model", f"/intake_analysis {intake.id} external"),
            ReplyAction("Add context", f"/intake_context {intake.id}"),
            ReplyAction("Intended root", f"/intake_root {intake.id}"),
            ReplyAction("Do not keep", f"/intake_discard {intake.id}"),
        )


def _external_search_page(importer, provider: str, argument: str, *, continuation: bool = False) -> str | PresentedReply:
    """Read one remote page; callbacks retain the exact query and opaque cursor."""
    prefix = provider.lower()
    query, token = argument.strip(), None
    if continuation:
        try:
            payload = json.loads(argument)
            if not isinstance(payload, list) or len(payload) != 2 or not all(isinstance(value, str) for value in payload):
                raise ValueError("Invalid cursor")
            query, token = payload
        except (ValueError, TypeError):
            return f"This search continuation is invalid. Start again with /{prefix}_search."
    if importer is None or not hasattr(importer, "search_page"):
        return f"{provider} search is not configured on this Steward process. Authorize {provider} locally first."
    restart = ReplyAction("Restart search", f"/{prefix}_search {query}")
    retry = f"/{prefix}_page {json.dumps([query, token])}" if continuation else restart.command
    try:
        page = importer.search_page(query, page_token=token)
        items = page.items
        lines = [f"{item.id}: {str(item.name if prefix == 'drive' else item.subject)[:300]}" for item in items]
        actions = [ReplyAction(f"Import {index}", f"/{prefix}_import {item.id}") for index, item in enumerate(items, 1)]
        if page.next_page_token:
            actions.append(ReplyAction("More results", f"/{prefix}_page {json.dumps([query, page.next_page_token])}"))
    except Exception:
        failure = _external_import_failure(f"{provider} search", retry)
        return replace(failure, actions=failure.actions + (restart,))
    if continuation:
        actions.append(restart)
    description = "\n".join(f"{index}. {line}" for index, line in enumerate(lines, 1))
    if not items:
        description = "No items on this page." if page.next_page_token else "No more matching items."
    return PresentedReply(
        f"{provider} results (metadata only):\n{description}\n\nSelect one item to import. Searching does not save content.",
        tuple(actions), title=f"{provider} search", icon="🔎",
    )


def _external_import_success(
    result: CaptureResult, provider: str, event: IncomingEvent,
    contexts: ReviewContextRepository | None,
) -> PresentedReply:
    """Offer explicit follow-ups for the exact retained source, including duplicates."""
    source = result.source
    message = (
        "Already imported. Open the existing source below; no new copy was saved."
        if result.duplicate else f"Imported from {provider} to Inbox. Choose what to do next."
    )
    if contexts is not None and source.id is not None:
        try:
            contexts.set(event.platform, event.chat_id, "source", source.id)
        except Exception:
            # The original has already been retained. Do not misreport this as
            # an import failure or invite a download retry for a context failure.
            message += "\nConversation selection could not be updated. Use the buttons below instead of referring to 'that'."
    actions = (
        ReplyAction("Read content", f"/source_content {source.id}"),
        ReplyAction("Source details", f"/source {source.id}"),
    ) if source.id is not None else ()
    return PresentedReply(message, actions, title=source.path.name, icon="✅",
                          reference=("source", source.id) if source.id is not None else None)


class DriveInboxImporter(Protocol):
    """Narrow boundary used by a transport command to import an explicit Drive ID."""

    def import_file(self, file_id: str) -> CaptureResult: ...


class StewardDriveImportApplication:
    """Turn a precise Telegram command into an explicit, local Drive import."""

    def __init__(self, importer: DriveInboxImporter | None, *, contexts: ReviewContextRepository | None = None) -> None:
        self._importer = importer
        self._contexts = contexts

    def handle_command(self, event: IncomingEvent) -> str | PresentedReply | None:
        command, separator, argument = (event.text or "").strip().partition(" ")
        command = command.partition("@")[0]
        if command == "/drive_search":
            return self._search(argument)
        if command == "/drive_page":
            return _external_search_page(self._importer, "Drive", argument, continuation=True)
        if command != "/drive_import":
            return None
        file_id = argument.strip()
        if not separator or not file_id or any(character.isspace() for character in file_id):
            return "Use /drive_import followed by one Google Drive file ID."
        if self._importer is None:
            return (
                "Drive import is not configured on this Steward process. "
                "Set STEWARD_GOOGLE_CLIENT_SECRETS, authorize Drive, then try again."
            )
        try:
            result = self._importer.import_file(file_id)
        except Exception:
            return _external_import_failure("Drive import", f"/drive_import {file_id}")
        return _external_import_success(result, "Drive", event, self._contexts)

    def _search(self, query: str) -> str | PresentedReply:
        return _external_search_page(self._importer, "Drive", query)


class GmailInboxImporter(Protocol):
    """Narrow boundary used by a transport command to import one Gmail ID."""

    def import_message(self, message_id: str) -> CaptureResult: ...


class StewardGmailImportApplication:
    """Turn a precise Telegram command into an explicit Gmail Inbox import."""

    def __init__(self, importer: GmailInboxImporter | None, *, contexts: ReviewContextRepository | None = None) -> None:
        self._importer = importer
        self._contexts = contexts

    def handle_command(self, event: IncomingEvent) -> str | PresentedReply | None:
        command, separator, argument = (event.text or "").strip().partition(" ")
        command = command.partition("@")[0]
        if command == "/gmail_search":
            return self._search(argument)
        if command == "/gmail_page":
            return _external_search_page(self._importer, "Gmail", argument, continuation=True)
        if command != "/gmail_import":
            return None
        message_id = argument.strip()
        if not separator or not message_id or any(character.isspace() for character in message_id):
            return "Use /gmail_import followed by one Gmail message ID."
        if self._importer is None:
            return (
                "Gmail import is not configured on this Steward process. "
                "Set STEWARD_GOOGLE_CLIENT_SECRETS, authorize Gmail, then try again."
            )
        try:
            result = self._importer.import_message(message_id)
        except Exception:
            return _external_import_failure("Gmail import", f"/gmail_import {message_id}")
        return _external_import_success(result, "Gmail", event, self._contexts)

    def _search(self, query: str) -> str | PresentedReply:
        return _external_search_page(self._importer, "Gmail", query)
