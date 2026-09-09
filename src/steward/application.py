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
from steward.extraction import InvalidSearchQueryError, SourceFragmentRepository
from steward.retrieval import LexicalSearchService
from steward.sources import SourceRepository
from steward.presentation import PresentedReply, ReplyAction
from steward.intake import ProvisionalIntakeService
from langchain_core.messages import HumanMessage, SystemMessage


TEXT_QUESTION_REQUIRED = "Send a text question and I will search your local knowledge."


class StewardReadApplication:
    """Provide deterministic, owner-facing Telegram reads over ordinary services.

    These commands do not involve an LLM, which makes them useful while a model
    provider is unavailable and keeps routine inspection out of model context.
    Pagination is deliberately text based for now; later callback buttons can
    invoke the same application methods without changing the domain boundary.
    """

    _PAGE_SIZE = 10

    def __init__(
        self,
        source_repository: SourceRepository,
        fragment_repository: SourceFragmentRepository,
        lexical_search: LexicalSearchService,
        workspace_repository: WorkspaceRepository,
        activity_service: ActivityService,
        inbox_dir: Path,
    ) -> None:
        self._sources = source_repository
        self._fragments = fragment_repository
        self._lexical = lexical_search
        self._workspaces = workspace_repository
        self._activity = activity_service
        self._inbox_dir = inbox_dir.resolve()

    def handle_command(self, event: IncomingEvent) -> str | PresentedReply | None:
        """Handle a bounded Telegram read command, or return ``None``."""
        command, _, argument = (event.text or "").strip().partition(" ")
        command = command.partition("@")[0].casefold()
        argument = argument.strip()
        if command == "/help":
            return self.help_text()
        if command == "/status":
            return self.status()
        if command == "/inbox":
            return self.inbox(self._page(argument))
        if command == "/sources":
            return self.sources(self._page(argument))
        if command == "/source":
            return self.source(argument)
        if command == "/workspaces":
            return self.workspaces()
        if command == "/activity":
            return self.activity(argument)
        if command == "/search":
            return self.search(argument)
        return None

    @staticmethod
    def help_text() -> str:
        return (
            "Steward commands (read-only):\n"
            "/status — local service summary\n"
            "/inbox [page] — saved Inbox sources\n"
            "/sources [page] — registered sources\n"
            "/source ID — one source and its extracted-text status\n"
            "/search TERMS — local lexical search\n"
            "/workspaces — current workspaces\n"
            "/activity [term] — recent audit events\n\n"
            "Writes remain reviewable: /save preserves text, and /approve_action "
            "or /reject_action reviews a pending proposal."
        )

    def status(self) -> str:
        active = self._sources.list_active()
        inbox_count = sum(self._is_inbox(source.path) for source in active)
        pending_activity = len(self._activity.list_recent(limit=20))
        return (
            "Steward is running locally.\n"
            f"Active sources: {len(active)}\n"
            f"Inbox sources: {inbox_count}\n"
            f"Workspaces: {len(self._workspaces.list_all())}\n"
            f"Recent activity events shown by /activity: {pending_activity}\n"
            "Use /help for available Telegram interactions."
        )

    def inbox(self, page: int) -> str | PresentedReply:
        sources = [source for source in self._sources.list_active() if self._is_inbox(source.path)]
        return self._source_list("Inbox", sources, page)

    def sources(self, page: int) -> str | PresentedReply:
        return self._source_list("Registered sources", self._sources.list_all(), page)

    def source(self, argument: str) -> str:
        try:
            source_id = int(argument)
        except ValueError:
            return "Use /source followed by a numeric source ID."
        source = self._sources.get_by_id(source_id)
        if source is None:
            return f"Source {source_id} was not found."
        fragments = self._fragments.list_for_source(source_id)
        return (
            f"Source {source.id}: {source.path.name}\n"
            f"Type: {source.source_type.value}\nStatus: {source.status.value}\n"
            f"Path: {source.path}\nExtracted fragments: {len(fragments)}"
        )

    def workspaces(self) -> str:
        workspaces = self._workspaces.list_all()
        if not workspaces:
            return "No workspaces yet. Ask me to create a workspace and I will make a reviewable proposal."
        return "Workspaces:\n" + "\n".join(
            f"{workspace.id}: {workspace.name} ({workspace.status})" for workspace in workspaces
        )

    def activity(self, query: str) -> str:
        needle = query.casefold()
        events = [
            event for event in self._activity.list_recent(limit=20)
            if not needle or needle in event.event_type.value or needle in event.details.casefold()
        ]
        if not events:
            return "No matching recent activity."
        return "Recent activity:\n" + "\n".join(
            f"{event.id}: {event.event_type.value} — {event.details or 'no details'}"
            for event in events
        )

    def search(self, query: str) -> str:
        if not query:
            return "Use /search followed by one or more terms."
        try:
            hits = self._lexical.search(query, limit=5)
        except InvalidSearchQueryError:
            return "Those search terms are not valid. Try plain words without search operators."
        if not hits:
            return f"No local source fragments matched: {query!r}."
        return "Search results:\n" + "\n".join(
            f"{hit.source.id}: {hit.source.path.name} — {hit.fragment.location} "
            f"[{hit.fragment.heading or 'Preamble'}]\n{(hit.highlighted_text or hit.fragment.text)[:180]}"
            for hit in hits
        )

    def _source_list(
        self, title: str, sources: list[Source], page: int
    ) -> str | PresentedReply:
        if not sources:
            return f"{title}: none."
        start = (page - 1) * self._PAGE_SIZE
        selected = sources[start : start + self._PAGE_SIZE]
        if not selected:
            return f"{title}: page {page} is empty."
        pages = (len(sources) + self._PAGE_SIZE - 1) // self._PAGE_SIZE
        lines = [f"{title} (page {page}/{pages}):"]
        lines.extend(f"{source.id}: {source.path.name} ({source.source_type.value}, {source.status.value})" for source in selected)
        command = "/inbox" if title == "Inbox" else "/sources"
        actions: list[ReplyAction] = []
        if page > 1:
            actions.append(ReplyAction("Back", f"{command} {page - 1}"))
        if page < pages:
            actions.append(ReplyAction("Next", f"{command} {page + 1}"))
        text = "\n".join(lines)
        return PresentedReply(text, tuple(actions)) if actions else text

    @staticmethod
    def _page(argument: str) -> int:
        if not argument:
            return 1
        try:
            return max(1, int(argument))
        except ValueError:
            return 1

    def _is_inbox(self, path: Path) -> bool:
        try:
            path.resolve().relative_to(self._inbox_dir)
        except ValueError:
            return False
        return True


class ToolAgentGraph(Protocol):
    """Minimal safe surface for the pre-built restricted tool graph."""

    def invoke(self, input: dict[str, object], config: dict[str, object]) -> dict[str, object]: ...


class StewardToolAgentApplication:
    """Expose an allowlisted ToolNode loop through an explicit Telegram command."""

    def __init__(self, graph: ToolAgentGraph) -> None:
        self._graph = graph

    def handle_command(self, event: IncomingEvent) -> str | None:
        command, separator, question = (event.text or "").strip().partition(" ")
        command = command.partition("@")[0]
        if command != "/agent":
            return None
        if not separator or not question.strip():
            return "Use /agent followed by a question that may need Steward's read-only tools."
        result = self._graph.invoke(
            {
                "messages": [
                    SystemMessage(
                        "You are Steward. Use only supplied allowlisted read-only tools when needed. "
                        "Never claim a tool result you did not receive; answer as soon as the result is sufficient."
                    ),
                    HumanMessage(question.strip()),
                ]
            },
            {"configurable": {"thread_id": f"tool-agent:{event.platform}:{event.chat_id}"}, "recursion_limit": 16},
        )
        messages = result.get("messages")
        if not isinstance(messages, list) or not messages:
            return "The tool agent returned no final response."
        return str(messages[-1].content)


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


class StewardProvisionalIntakeApplication:
    """Present staged attachment intake as a reviewable save-or-discard choice."""

    def __init__(self, service: ProvisionalIntakeService) -> None:
        self._service = service

    def begin_file(self, event: IncomingEvent, original_path: Path) -> PresentedReply:
        intake = self._service.stage_file(event, original_path)
        if intake.status == "accepted":
            return PresentedReply("This attachment was already saved.")
        if intake.status == "discarded":
            return PresentedReply("This attachment was previously discarded. Send it again to reconsider it.")
        return PresentedReply(
            f"Provisional intake {intake.id}: {intake.summary}\n\n"
            "It is staged locally and has not been added to your Inbox.",
            (
                ReplyAction("Save to Inbox", f"/intake_accept {intake.id}"),
                ReplyAction("Add context", f"/intake_context {intake.id}"),
                ReplyAction("Do not keep", f"/intake_discard {intake.id}"),
            ),
        )

    def begin_text(self, event: IncomingEvent) -> PresentedReply:
        intake = self._service.stage_text(event)
        return PresentedReply(
            f"Provisional intake {intake.id}: {intake.summary}\n\n"
            "It is staged locally and has not been added to your Inbox.",
            (
                ReplyAction("Save to Inbox", f"/intake_accept {intake.id}"),
                ReplyAction("Add context", f"/intake_context {intake.id}"),
                ReplyAction("Do not keep", f"/intake_discard {intake.id}"),
            ),
        )

    @staticmethod
    def should_propose_text(event: IncomingEvent) -> bool:
        """Keep ordinary conversational messages out of durable intake by default."""
        text = (event.text or "").strip()
        return (
            len(text) >= 280
            or text.count("\n") >= 2
            or text.casefold().startswith(("note:", "thought:", "remember:", "deadline:"))
        )

    def handle_command(self, event: IncomingEvent) -> CaptureResult | str | None:
        command, separator, argument = (event.text or "").strip().partition(" ")
        command = command.partition("@")[0]
        if command not in {"/intake_accept", "/intake_discard", "/intake_context"}:
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
                    return (
                        "Use /intake_context followed by the intake ID and what it relates to. "
                        "For example: /intake_context 4 CS3210 OpenMP assignment"
                    )
                intake = self._service.add_context(intake_id, event.chat_id, context)
                return PresentedReply(
                    f"Updated provisional intake {intake.id}: {intake.summary}",
                    (
                        ReplyAction("Save to Inbox", f"/intake_accept {intake.id}"),
                        ReplyAction("Do not keep", f"/intake_discard {intake.id}"),
                    ),
                )
            intake = self._service.discard(intake_id, event.chat_id)
        except (OSError, ValueError) as error:
            return str(error)
        return f"Discarded provisional intake {intake.id}; its staged copy was removed."


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
        source_repository: SourceRepository | None = None,
        inbox_dir: Path | None = None,
    ) -> None:
        self._proposals = proposal_repository
        self._workspaces = workspace_repository
        self._threads = thread_repository
        self._activity = activity_service
        self._graph = approval_graph
        self._proposal_builder = proposal_builder
        self._sources = source_repository
        self._inbox_dir = inbox_dir.resolve() if inbox_dir is not None else None

    def begin(self, event: IncomingEvent, result: CaptureResult) -> str | PresentedReply | None:
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
        return PresentedReply(
            f"Organization proposal {proposal_id}: {proposal.rationale}\n"
            f"Suggested destination: {destination}\n\n"
            "Accepting is the only action that may move this original file.",
            (
                ReplyAction("Accept", f"/organization_accept {proposal_id}"),
                ReplyAction("Reject", f"/organization_reject {proposal_id}"),
            ),
        )

    def handle_decision(self, event: IncomingEvent) -> str | None:
        """Resume exactly this chat's paused graph for an explicit decision."""

        pending = self._threads.get_pending(event.platform, event.chat_id)
        if pending is None:
            return None
        response = (event.text or "").strip().casefold()
        command, _, argument = response.partition(" ")
        if command == "/organization_accept" and argument.isdigit() and int(argument) == pending.proposal_id:
            decision = "accepted"
        elif command == "/organization_reject" and argument.isdigit() and int(argument) == pending.proposal_id:
            decision = "rejected"
        elif response in {"accept", "accepted"}:
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

    def begin_inbox_review(self, event: IncomingEvent) -> str | PresentedReply:
        """Create one durable, approval-paused organization proposal from Inbox."""
        if self._sources is None or self._inbox_dir is None:
            return "Inbox organization is not configured for this Steward process."
        inbox_sources = [
            source for source in self._sources.list_active() if self._is_inbox(source.path)
        ]
        if not inbox_sources:
            return "Your Inbox has no active registered sources to organize."
        for source in inbox_sources:
            response = self.begin(event, CaptureResult(source, duplicate=False))
            response_text = response.text if isinstance(response, PresentedReply) else response
            if response_text != "No confident organization match was found, so the source remains in Inbox.":
                return response or "No Inbox organization proposal was created."
        return (
            f"I inspected {len(inbox_sources)} Inbox source(s), but found no confident "
            "workspace match. They remain in Inbox."
        )

    def _is_inbox(self, path: Path) -> bool:
        if self._inbox_dir is None:
            return False
        try:
            path.resolve().relative_to(self._inbox_dir)
        except ValueError:
            return False
        return True


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
        if command == "/create_workspace":
            return self.propose_workspace(argument)
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

    def propose_workspace(self, name: str) -> str:
        """Create a durable workspace proposal without creating the workspace."""
        try:
            proposal, workspace = self._service.propose_workspace_creation(name)
        except ValueError as error:
            return str(error)
        if workspace is not None:
            return f"Workspace {workspace.id}: {workspace.name} already exists."
        if proposal is None or proposal.id is None:
            raise RuntimeError("Workspace creation did not return a proposal.")
        return (
            f"Workspace proposal {proposal.id}: create '{proposal.payload['name']}'. "
            f"Review with /approve_action {proposal.id} or /reject_action {proposal.id}."
        )


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
        read_application: StewardReadApplication | None = None,
        provisional_intake_application: StewardProvisionalIntakeApplication | None = None,
        tool_agent_application: StewardToolAgentApplication | None = None,
    ) -> None:
        self._question_application = question_application
        self._capture_application = capture_application
        self._intent_resolver = intent_resolver or IntentResolver()
        self._organization_approval_application = organization_approval_application
        self._action_proposal_application = action_proposal_application
        self._drive_import_application = drive_import_application
        self._gmail_import_application = gmail_import_application
        self._read_application = read_application
        self._provisional_intake_application = provisional_intake_application
        self._tool_agent_application = tool_agent_application

    def handle(self, event: IncomingEvent) -> str:
        if self._tool_agent_application is not None:
            tool_response = self._tool_agent_application.handle_command(event)
            if tool_response is not None:
                return tool_response
        if self._provisional_intake_application is not None:
            intake_response = self._provisional_intake_application.handle_command(event)
            if isinstance(intake_response, CaptureResult):
                return self._capture_with_optional_proposal(event, intake_response)
            if intake_response is not None:
                return intake_response
        if self._read_application is not None:
            read_response = self._read_application.handle_command(event)
            if read_response is not None:
                return read_response
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
        if decision.primary_intent is Intent.SEARCH:
            if self._read_application is None:
                return "Local search is not configured for this Steward process."
            return self._read_application.search(
                self._search_terms(event.text or "", natural_language=True)
            )
        if decision.primary_intent is Intent.INSPECT:
            if self._read_application is None:
                return "Local inspection is not configured for this Steward process."
            if "inbox" in decision.referenced_objects:
                return self._read_application.inbox(1)
            if "activity" in decision.referenced_objects:
                return self._read_application.activity("")
            return "Tell me whether you want to inspect your Inbox, a source ID, workspaces, or activity."
        if decision.primary_intent is Intent.ORGANIZE:
            if self._organization_approval_application is not None:
                return self._organization_approval_application.begin_inbox_review(event)
            if self._read_application is not None:
                return self._read_application.inbox(1)
            return "Inbox organization is not configured for this Steward process."
        if decision.primary_intent is Intent.CREATE_WORKSPACE:
            if self._action_proposal_application is None:
                return "Workspace proposals are not configured for this Steward process."
            return self._action_proposal_application.propose_workspace(
                self._workspace_name(event.text or "")
            )
        if decision.primary_intent is Intent.ASK:
            return self._question_application.handle(event)
        if decision.primary_intent is Intent.CAPTURE:
            try:
                result = self._capture_application.capture(event)
            except ValueError as error:
                return str(error)
            return self._capture_with_optional_proposal(event, result)
        if (
            decision.primary_intent is Intent.UNKNOWN
            and self._provisional_intake_application is not None
            and self._provisional_intake_application.should_propose_text(event)
        ):
            return self._provisional_intake_application.begin_text(event)
        return (
            "I am not sure which Steward action you want. Try /help, ask a question, "
            "or say `find ...`, `organize my inbox`, or `create a workspace for ...`."
        )

    def handle_file(self, event: IncomingEvent, original_path: Path) -> str:
        """Documents are deterministic capture signals after adapter validation."""
        if (event.text or "").strip().startswith("/save"):
            return self._capture_with_optional_proposal(
                event, self._capture_application.capture_file(event, original_path)
            )
        if self._provisional_intake_application is not None:
            return self._provisional_intake_application.begin_file(event, original_path)
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
        if isinstance(proposal, PresentedReply):
            return PresentedReply(f"{saved}\n\n{proposal.text}", proposal.actions)
        return f"{saved}\n\n{proposal}" if proposal is not None else saved

    @staticmethod
    def _search_terms(text: str, *, natural_language: bool = False) -> str:
        normalized = text.strip()
        for prefix in ("find ", "search ", "look for "):
            if normalized.casefold().startswith(prefix):
                normalized = normalized[len(prefix):].strip()
                break
        if not natural_language:
            return normalized
        words = [
            word for word in normalized.replace("'", "").split()
            if word.casefold() not in {"a", "an", "about", "for", "in", "my", "notes", "on", "the"}
        ]
        return " OR ".join(words) or normalized

    @staticmethod
    def _workspace_name(text: str) -> str:
        normalized = text.strip()
        for prefix in (
            "create a workspace for ",
            "create workspace for ",
            "create workspace ",
            "new workspace ",
        ):
            if normalized.casefold().startswith(prefix):
                return normalized[len(prefix):].strip()
        return normalized
