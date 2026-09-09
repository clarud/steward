"""Application-level use cases composed from Steward domain services."""

from __future__ import annotations

from typing import Callable, NotRequired, Protocol, TypedDict

from steward.answer import AnswerCitation
from steward.capture import CaptureResult, InboxCaptureService
from pathlib import Path
from steward.events import IncomingEvent
from steward.intent import Intent, IntentResolver
from steward.organization import (
    OrganizationApprovalThreadRepository,
    OrganizationProposal,
    OrganizationProposalRepository,
    OrganizationService,
)
from steward.sources import Source
from steward.workspaces import Workspace
from steward.workspaces import WorkspaceRepository
from steward.activity import ActivityService, ActivityType
from steward.action_proposals import ActionProposalRepository, ActionProposalService
from steward.calendar import CalendarEventProposalService, CalendarWriteService


TEXT_QUESTION_REQUIRED = "Send a text question and I will search your local knowledge."


class QuestionGraph(Protocol):
    """The small graph interface needed by the text-question use case."""

    def invoke(self, input: dict[str, str], config: dict[str, object]) -> "QuestionResult": ...


class QuestionResult(TypedDict):
    """The part of graph output that the transport needs to return."""

    answer: str
    citations: NotRequired[tuple[AnswerCitation, ...]]


class StewardQuestionApplication:
    """Answer one normalized text event through Steward's retrieval graph."""

    def __init__(self, graph: QuestionGraph) -> None:
        self._graph = graph

    def handle(self, event: IncomingEvent) -> str:
        """Return a response without knowing which external platform sent it."""

        if not event.text or not event.text.strip():
            return TEXT_QUESTION_REQUIRED

        result = self._graph.invoke(
            {"question": event.text},
            {"configurable": {"thread_id": f"{event.platform}:{event.chat_id}"}},
        )
        return self._format_response(result)

    @staticmethod
    def _format_response(result: QuestionResult) -> str:
        """Keep generated citation keys useful on a text-only transport."""

        citations = result.get("citations", ())
        if not citations:
            return result["answer"]

        source_lines = []
        for citation in citations:
            heading = citation.heading or "Preamble"
            source_lines.append(
                f"[{citation.key}] {citation.source_path}:"
                f"{citation.location} [{heading}]"
            )
        return f"{result['answer']}\n\nSources:\n" + "\n".join(source_lines)


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
            return f"Already saved: {result.source.path}"
        return f"Saved to Inbox: {result.source.path}"

    def handle_file(self, event: IncomingEvent, original_path: Path) -> str:
        """Preserve a document already downloaded by a transport adapter."""

        return self.format_result(self.capture_file(event, original_path))

    def capture_file(self, event: IncomingEvent, original_path: Path) -> CaptureResult:
        """Persist a downloaded document and expose its result to follow-on workflows."""

        return self._capture_service.capture_file(event, original_path)


class DriveInboxImporter(Protocol):
    """Narrow boundary used by a transport command to import an explicit Drive ID."""

    def import_file(self, file_id: str) -> CaptureResult: ...


class StewardDriveImportApplication:
    """Turn a precise Telegram command into an explicit, local Drive import."""

    def __init__(self, importer: DriveInboxImporter | None) -> None:
        self._importer = importer

    def handle_command(self, event: IncomingEvent) -> str | None:
        command, separator, argument = (event.text or "").strip().partition(" ")
        command = command.partition("@")[0]
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
        except (OSError, ValueError) as error:
            return f"Drive import failed: {error}"
        status = "Already imported" if result.duplicate else "Imported Drive file to Inbox"
        return f"{status}: {result.source.path}"


class GmailInboxImporter(Protocol):
    """Narrow boundary used by a transport command to import one Gmail ID."""

    def import_message(self, message_id: str) -> CaptureResult: ...


class StewardGmailImportApplication:
    """Turn a precise Telegram command into an explicit Gmail Inbox import."""

    def __init__(self, importer: GmailInboxImporter | None) -> None:
        self._importer = importer

    def handle_command(self, event: IncomingEvent) -> str | None:
        command, separator, argument = (event.text or "").strip().partition(" ")
        command = command.partition("@")[0]
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
        except (OSError, ValueError) as error:
            return f"Gmail import failed: {error}"
        status = "Already imported" if result.duplicate else "Imported Gmail message to Inbox"
        return f"{status}: {result.source.path}"


class OrganizationApprovalGraph(Protocol):
    """The small resumable graph surface required by the approval application."""

    def invoke(self, input: object, config: dict[str, object]) -> dict[str, object]: ...


class StewardOrganizationApprovalApplication:
    """Bridge capture and explicit Telegram organization decisions safely."""

    def __init__(
        self,
        proposal_repository: OrganizationProposalRepository,
        workspace_repository: WorkspaceRepository,
        thread_repository: OrganizationApprovalThreadRepository,
        activity_service: ActivityService,
        approval_graph: OrganizationApprovalGraph,
        proposal_builder: Callable[[Source, list[Workspace]], OrganizationProposal] | None = None,
    ) -> None:
        self._proposals = proposal_repository
        self._workspaces = workspace_repository
        self._threads = thread_repository
        self._activity = activity_service
        self._graph = approval_graph
        self._proposal_builder = proposal_builder

    def begin(self, event: IncomingEvent, result: CaptureResult) -> str | None:
        """Persist a proposal and pause its graph before any file mutation."""

        if result.duplicate:
            return None
        pending = self._threads.get_pending(event.platform, event.chat_id)
        if pending is not None:
            return (
                f"Saved to Inbox, but proposal {pending.proposal_id} is still awaiting your decision. "
                "Reply `accept` or `reject` first."
            )
        workspaces = self._workspaces.list_all()
        proposal = (
            self._proposal_builder(result.source, workspaces)
            if self._proposal_builder is not None
            else OrganizationService().propose(result.source, workspaces)
        )
        if proposal.suggested_path is None:
            return "No confident organization match was found, so the source remains in Inbox."
        proposal_id = self._proposals.add(proposal)
        self._activity.record(
            ActivityType.ORGANIZATION_PROPOSED,
            object_id=str(proposal_id),
            details=proposal.rationale,
        )
        thread_id = f"approval:{event.platform}:{event.chat_id}:{proposal_id}"
        self._threads.start(event.platform, event.chat_id, proposal_id, thread_id)
        paused = self._graph.invoke(
            {"proposal_id": proposal_id},
            {"configurable": {"thread_id": thread_id}},
        )
        if "__interrupt__" not in paused:
            raise RuntimeError("Organization approval graph did not pause for a decision.")
        destination = str(proposal.suggested_path) if proposal.suggested_path else "Inbox"
        return (
            f"Organization proposal {proposal_id}: {proposal.rationale}\n"
            f"Suggested destination: {destination}\n\n"
            "Reply `accept` or `reject`."
        )

    def handle_decision(self, event: IncomingEvent) -> str | None:
        """Resume exactly this chat's paused graph for an explicit decision."""

        pending = self._threads.get_pending(event.platform, event.chat_id)
        if pending is None:
            return None
        response = (event.text or "").strip().casefold()
        if response in {"accept", "accepted"}:
            decision = "accepted"
        elif response in {"reject", "rejected"}:
            decision = "rejected"
        else:
            return (
                f"Proposal {pending.proposal_id} is awaiting your decision. "
                "Reply exactly `accept` or `reject`."
            )
        from langgraph.types import Command

        completed = self._graph.invoke(
            Command(resume=decision),
            {"configurable": {"thread_id": pending.thread_id}},
        )
        if completed.get("status") != decision:
            raise RuntimeError("Organization approval did not reach a final status.")
        self._threads.finish(event.platform, event.chat_id, decision)
        return f"Proposal {pending.proposal_id} {decision}."


class StewardActionProposalApplication:
    """Review durable agent action proposals from an authorized transport chat.

    A proposal is persisted before this application sees it, so the user can
    review it after a restart without replaying any model reasoning. The narrow
    command grammar keeps approval deterministic: conversational model output
    cannot become a write operation.
    """

    def __init__(
        self,
        repository: ActionProposalRepository,
        service: ActionProposalService,
        calendar_proposals: CalendarEventProposalService | None = None,
        calendar_writer_factory: Callable[[], CalendarWriteService] | None = None,
    ) -> None:
        self._repository = repository
        self._service = service
        self._calendar_proposals = calendar_proposals
        self._calendar_writer_factory = calendar_writer_factory

    def handle_command(self, event: IncomingEvent) -> str | None:
        text = (event.text or "").strip()
        if text == "/action_proposals":
            pending = [proposal for proposal in self._repository.list_all() if proposal.status == "pending"]
            if not pending:
                return "There are no pending action proposals."
            lines = ["Pending action proposals:"]
            for proposal in pending:
                lines.append(
                    f"{proposal.id}: {proposal.action_type} {proposal.payload}\n"
                    f"Reply /approve_action {proposal.id} or /reject_action {proposal.id}."
                )
            return "\n\n".join(lines)

        command, separator, argument = text.partition(" ")
        command = command.partition("@")[0]
        if command not in {"/approve_action", "/reject_action"}:
            return None
        if not separator or not argument.strip().isdigit():
            return f"Use {command} followed by a numeric proposal ID."
        proposal_id = int(argument.strip())
        decision = "accepted" if command == "/approve_action" else "rejected"
        proposal = self._repository.get(proposal_id)
        if proposal is not None and proposal.action_type == CalendarEventProposalService.CREATE_TRAVEL_EVENT:
            if self._calendar_proposals is None:
                return "Calendar proposal review is not configured on this Steward process."
            try:
                writer = self._calendar_writer_factory() if decision == "accepted" and self._calendar_writer_factory else None
                reviewed = self._calendar_proposals.review(proposal_id, decision, writer)
            except ValueError as error:
                return str(error)
            return f"Calendar proposal {reviewed.id} {reviewed.status}."
        try:
            proposal, workspace = self._service.review(proposal_id, decision)
        except ValueError as error:
            return str(error)
        if workspace is not None:
            return (
                f"Action proposal {proposal.id} accepted. "
                f"Workspace {workspace.id}: {workspace.name} is available."
            )
        return f"Action proposal {proposal.id} {proposal.status}."


class StewardEventApplication:
    """Route normalized events through one explicit intent decision."""

    def __init__(
        self,
        question_application: StewardQuestionApplication,
        capture_application: StewardCaptureApplication,
        intent_resolver: IntentResolver | None = None,
        organization_approval_application: StewardOrganizationApprovalApplication | None = None,
        action_proposal_application: StewardActionProposalApplication | None = None,
        drive_import_application: StewardDriveImportApplication | None = None,
        gmail_import_application: StewardGmailImportApplication | None = None,
    ) -> None:
        self._question_application = question_application
        self._capture_application = capture_application
        self._intent_resolver = intent_resolver or IntentResolver()
        self._organization_approval_application = organization_approval_application
        self._action_proposal_application = action_proposal_application
        self._drive_import_application = drive_import_application
        self._gmail_import_application = gmail_import_application

    def handle(self, event: IncomingEvent) -> str:
        if self._drive_import_application is not None:
            drive_response = self._drive_import_application.handle_command(event)
            if drive_response is not None:
                return drive_response
        if self._gmail_import_application is not None:
            gmail_response = self._gmail_import_application.handle_command(event)
            if gmail_response is not None:
                return gmail_response
        if self._action_proposal_application is not None:
            action_response = self._action_proposal_application.handle_command(event)
            if action_response is not None:
                return action_response
        if self._organization_approval_application is not None:
            decision_response = self._organization_approval_application.handle_decision(event)
            if decision_response is not None:
                return decision_response
        decision = self._intent_resolver.resolve(event)
        if decision.primary_intent is Intent.ASK:
            return self._question_application.handle(event)
        if decision.primary_intent is Intent.CAPTURE:
            try:
                result = self._capture_application.capture(event)
            except ValueError as error:
                return str(error)
            return self._capture_with_optional_proposal(event, result)
        return "I do not yet know how to safely handle that request."

    def handle_file(self, event: IncomingEvent, original_path: Path) -> str:
        """Documents are deterministic capture signals after adapter validation."""

        return self._capture_with_optional_proposal(
            event, self._capture_application.capture_file(event, original_path)
        )

    def _capture_with_optional_proposal(
        self, event: IncomingEvent, result: CaptureResult
    ) -> str:
        saved = self._capture_application.format_result(result)
        if self._organization_approval_application is None:
            return saved
        proposal = self._organization_approval_application.begin(event, result)
        return f"{saved}\n\n{proposal}" if proposal is not None else saved
