"""Application-level use cases composed from Steward domain services."""

from __future__ import annotations

import os
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
from steward.calendar import CalendarEventProposalService, CalendarService, CalendarWriteService
from steward.extraction import InvalidSearchQueryError, SourceFragmentRepository
from steward.retrieval import LexicalSearchService
from steward.sources import SourceRepository
from steward.presentation import PresentedReply, ReplyAction
from steward.intake import ProvisionalIntakeService
from langchain_core.messages import HumanMessage, SystemMessage
from steward.records import RecordService
from steward.knowledge import KnowledgeEnrichmentProposalRepository, KnowledgeService
from steward.roots import SourceRootRepository
from steward.privacy import PrivacyRule, PrivacyService
from steward.telegram import TelegramUpdateDeliveryRepository
from steward.tasks import TaskService
from steward.research import ResearchProvider, ResearchProviderError, ResearchRetentionService, ResearchService


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
        action_proposals: ActionProposalRepository | None = None,
        deliveries: TelegramUpdateDeliveryRepository | None = None,
    ) -> None:
        self._sources = source_repository
        self._fragments = fragment_repository
        self._lexical = lexical_search
        self._workspaces = workspace_repository
        self._activity = activity_service
        self._inbox_dir = inbox_dir.resolve()
        self._action_proposals = action_proposals
        self._deliveries = deliveries

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
            "Steward Telegram guide\n\n"
            "Read-only:\n"
            "/status — local service summary\n"
            "/inbox [page] — saved Inbox sources\n"
            "/sources [page] — registered sources\n"
            "/source ID — one source and its extracted-text status\n"
            "/search TERMS — local lexical search\n"
            "/workspaces — current workspaces\n"
            "/activity [term] — recent audit events\n"
            "/records, /tasks, /roots — saved state\n"
            "/calendar_search [terms], /calendar_get ID — current Google Calendar\n\n"
            "Explicit actions (they create a review or a selected import):\n"
            "/propose_task TEXT, /complete_task ID\n"
            "Natural task capture: `remind me to â€¦`, `todo: â€¦`, `task: â€¦`, or `deadline: â€¦`\n"
            "/propose_note TEXT, /research QUESTION\n"
            "/propose_travel_record SOURCE_ID\n"
            "/propose_receipt_record SOURCE_ID\n"
            "/propose_warranty_record SOURCE_ID\n"
            "/correct_travel_record RECORD_ID FIELD VALUE\n"
            "/correct_receipt_record RECORD_ID FIELD VALUE\n"
            "/correct_warranty_record RECORD_ID FIELD VALUE\n"
            "/calendar_travel RECORD_ID, /calendar_task TASK_ID\n"
            "/drive_search QUERY, /gmail_search QUERY\n"
            "/privacy SOURCE_ID, /set_privacy SOURCE_ID RULE\n\n"
            "Review-required writes use the buttons or /approve_action ID and "
            "/reject_action ID. /save remains an explicit immediate Inbox shortcut.\n\n"
            "Admin diagnostics: /deliveries, /delivery_history, /dead_letters\n"
            "Dead-letter recovery: /recover_dead_letter UPDATE_ID (creates a review; never replays a message)"
        )

    def status(self) -> str:
        active = self._sources.list_active()
        inbox_count = sum(self._is_inbox(source.path) for source in active)
        pending_activity = len(self._activity.list_recent(limit=20))
        lines = [
            "Steward is running locally.\n"
            f"Active sources: {len(active)}\n"
            f"Inbox sources: {inbox_count}\n"
            f"Workspaces: {len(self._workspaces.list_all())}\n"
            f"Recent activity events shown by /activity: {pending_activity}"
        ]
        if self._action_proposals is not None:
            pending = sum(proposal.status == "pending" for proposal in self._action_proposals.list_all())
            lines.append(f"Pending action reviews: {pending}")
        if self._deliveries is not None:
            recent = self._deliveries.list_recent(limit=20)
            processing = sum(delivery.status == "processing" for delivery in recent)
            dead_letters = len(self._deliveries.list_dead_letters(limit=20))
            lines.append(f"Telegram delivery: {processing} processing, {dead_letters} dead letters")
        lines.append("Use /help for available Telegram interactions.")
        return "\n".join(lines)

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

    def handle_command(self, event: IncomingEvent) -> str | PresentedReply | None:
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


class StewardRecordApplication:
    """Render evidence-backed record reads and extraction previews for Telegram."""

    CREATE_TRAVEL_RECORD = "create_travel_record"
    CREATE_RECEIPT_RECORD = "create_receipt_record"
    CREATE_WARRANTY_RECORD = "create_warranty_record"
    CORRECT_TRAVEL_RECORD = "correct_travel_record"
    CORRECT_RECEIPT_RECORD = "correct_receipt_record"
    CORRECT_WARRANTY_RECORD = "correct_warranty_record"

    def __init__(
        self,
        records: RecordService,
        fragments: SourceFragmentRepository,
        proposals: ActionProposalRepository,
        activity: ActivityService,
    ) -> None:
        self._records = records
        self._fragments = fragments
        self._proposals = proposals
        self._activity = activity

    def handle_command(self, event: IncomingEvent) -> str | None:
        command, separator, argument = (event.text or "").strip().partition(" ")
        command = command.partition("@")[0]
        if command == "/records":
            return self._list_records()
        if command in {"/propose_receipt_record", "/propose_warranty_record"}:
            return self._propose_document_record(command, separator, argument)
        if command in {"/correct_travel_record", "/correct_receipt_record", "/correct_warranty_record"}:
            return self._propose_record_correction(command, separator, argument)
        if command != "/propose_travel_record":
            return None
        if not separator or not argument.strip().isdigit():
            return "Use /propose_travel_record followed by a numeric source ID."
        source_id = int(argument.strip())
        fragments = self._fragments.list_for_source(source_id)
        if not fragments:
            return f"Source {source_id} has no extracted text to interpret as a travel record."
        proposal = self._records.propose_travel_record(
            source_id, [(fragment.id or 0, fragment.text) for fragment in fragments]
        )
        if not proposal.field_evidence:
            return f"Source {source_id} did not yield evidenced travel fields."
        record = proposal.record
        fields = (
            ("flight", "flight_number", record.flight_number),
            ("departure", "departure", record.departure),
            ("arrival", "arrival", record.arrival),
            ("departure time", "departure_time", record.departure_time.isoformat() if record.departure_time else None),
            ("arrival time", "arrival_time", record.arrival_time.isoformat() if record.arrival_time else None),
            ("booking reference", "booking_reference", record.booking_reference),
        )
        rendered = "\n".join(
            f"{label}: {value} (fragment {proposal.field_evidence[field]})"
            for label, field, value in fields
            if value is not None and field in proposal.field_evidence
        )
        pending = self._proposals.find_pending(
            self.CREATE_TRAVEL_RECORD, {"source_id": str(source_id)}
        )
        if pending is None:
            pending = self._proposals.add(self.CREATE_TRAVEL_RECORD, {"source_id": str(source_id)})
            self._activity.record(
                ActivityType.ACTION_PROPOSED,
                object_id=str(pending.id),
                details=f"Create travel record from source {source_id}",
            )
        return PresentedReply(
            f"Travel record preview from source {source_id}:\n{rendered}\n\n"
            f"Proposal {pending.id}: no travel record has been saved.",
            (
                ReplyAction("Accept record", f"/approve_action {pending.id}"),
                ReplyAction("Reject", f"/reject_action {pending.id}"),
            ),
        )

    def _propose_record_correction(self, command: str, separator: str, argument: str) -> str | PresentedReply:
        record_id, field_separator, remainder = argument.strip().partition(" ")
        field, value_separator, value = remainder.partition(" ")
        if not separator or not record_id.isdigit() or not field_separator or not value_separator:
            return f"Use {command} followed by record ID, field, and replacement value."
        record_type, action_type, records, validate = {
            "/correct_travel_record": ("Travel", self.CORRECT_TRAVEL_RECORD, self._records.list_travel_records, self._records.validate_travel_field),
            "/correct_receipt_record": ("Receipt", self.CORRECT_RECEIPT_RECORD, self._records.list_receipt_records, self._records.validate_receipt_field),
            "/correct_warranty_record": ("Warranty", self.CORRECT_WARRANTY_RECORD, self._records.list_warranty_records, self._records.validate_warranty_field),
        }[command]
        record = next((item for item in records() if item.id == int(record_id)), None)
        if record is None:
            return f"{record_type} record {record_id} was not found."
        try:
            validate(field, value)
        except ValueError as error:
            return str(error)
        payload = {"record_id": record_id, "field": field, "value": value}
        pending = self._proposals.find_pending(action_type, payload)
        if pending is None:
            pending = self._proposals.add(action_type, payload)
            self._activity.record(ActivityType.ACTION_PROPOSED, object_id=str(pending.id), details=f"Correct {record_type.casefold()} record {record_id} field {field}")
        return PresentedReply(
            f"{record_type} correction proposal {pending.id}: record {record_id} {field} → {value}.\n\nThe record is unchanged until approval.",
            (ReplyAction("Apply correction", f"/approve_action {pending.id}"), ReplyAction("Reject", f"/reject_action {pending.id}")),
        )

    def _propose_document_record(self, command: str, separator: str, argument: str) -> str | PresentedReply:
        label = "receipt" if command == "/propose_receipt_record" else "warranty"
        action_type = self.CREATE_RECEIPT_RECORD if label == "receipt" else self.CREATE_WARRANTY_RECORD
        if not separator or not argument.strip().isdigit():
            return f"Use {command} followed by a numeric source ID."
        source_id = int(argument.strip())
        fragments = self._fragments.list_for_source(source_id)
        if not fragments:
            return f"Source {source_id} has no extracted text to interpret as a {label} record."
        proposal = (
            self._records.propose_receipt_record(source_id, [(item.id or 0, item.text) for item in fragments])
            if label == "receipt"
            else self._records.propose_warranty_record(source_id, [(item.id or 0, item.text) for item in fragments])
        )
        if not proposal.field_evidence:
            return f"Source {source_id} did not yield evidenced {label} fields."
        fields = [
            f"{field}: {getattr(proposal.record, field)} (fragment {fragment_id})"
            for field, fragment_id in proposal.field_evidence.items()
            if getattr(proposal.record, field) is not None
        ]
        pending = self._proposals.find_pending(action_type, {"source_id": str(source_id)})
        if pending is None:
            pending = self._proposals.add(action_type, {"source_id": str(source_id)})
            self._activity.record(ActivityType.ACTION_PROPOSED, object_id=str(pending.id), details=f"Create {label} record from source {source_id}")
        return PresentedReply(
            f"{label.title()} record preview from source {source_id}:\n" + "\n".join(fields)
            + f"\n\nProposal {pending.id}: no {label} record has been saved.",
            (ReplyAction("Accept record", f"/approve_action {pending.id}"), ReplyAction("Reject", f"/reject_action {pending.id}")),
        )

    def _list_records(self) -> str:
        lines: list[str] = []
        lines.extend(
            f"Travel {record.id}: {record.flight_number or '(flight unknown)'} "
            f"{record.departure or '?'} → {record.arrival or '?'}"
            for record in self._records.list_travel_records()
        )
        lines.extend(
            f"Receipt {record.id}: {record.merchant or '(merchant unknown)'}"
            for record in self._records.list_receipt_records()
        )
        lines.extend(
            f"Warranty {record.id}: {record.product_name or '(product unknown)'}"
            for record in self._records.list_warranty_records()
        )
        return "Saved records:\n" + "\n".join(lines) if lines else "No saved records."


class StewardTaskApplication:
    """Turn explicit Telegram commitments into reviewable task proposals."""

    CREATE_TASK = "create_task"

    def __init__(self, tasks: TaskService, proposals: ActionProposalRepository, activity: ActivityService) -> None:
        self._tasks = tasks
        self._proposals = proposals
        self._activity = activity

    def handle_command(self, event: IncomingEvent) -> str | PresentedReply | None:
        command, separator, argument = (event.text or "").strip().partition(" ")
        command = command.partition("@")[0]
        if command == "/tasks":
            tasks = self._tasks.list_open()
            if not tasks:
                return "No open tasks."
            return "Open tasks:\n" + "\n".join(
                f"{task.id}: {task.title}"
                + (f" (due {task.due_at.isoformat()})" if task.due_at else "")
                + (f" ({task.due_hint})" if task.due_hint else "")
                for task in tasks
            )
        if command == "/complete_task":
            if not separator or not argument.strip().isdigit():
                return "Use /complete_task followed by a numeric task ID."
            task_id = int(argument.strip())
            existing = self._tasks.get(task_id)
            if existing is not None and existing.status == "completed":
                return f"Task {existing.id} was already completed: {existing.title}."
            try:
                task = self._tasks.complete(task_id)
            except ValueError as error:
                return str(error)
            self._activity.record(ActivityType.TASK_COMPLETED, object_id=str(task.id), details=task.title)
            return f"Task {task.id} completed: {task.title}."
        if command != "/propose_task":
            return None
        if not separator:
            return "Use /propose_task followed by what you need to do."
        return self.propose(argument)

    def propose(self, text: str) -> str | PresentedReply:
        try:
            title, due_hint, due_at = self._tasks.parse_proposal_with_due_at(text)
        except ValueError as error:
            return str(error)
        payload = {"title": title, "due_hint": due_hint or "", "due_at": due_at.isoformat() if due_at else ""}
        pending = self._proposals.find_pending(self.CREATE_TASK, payload)
        if pending is None:
            pending = self._proposals.add(self.CREATE_TASK, payload)
            self._activity.record(ActivityType.ACTION_PROPOSED, object_id=str(pending.id), details=f"Create task: {title}")
        due_line = f"\nDue cue: {due_hint}" if due_hint else ""
        if due_at:
            due_line += f"\nDue at: {due_at.isoformat()}"
        return PresentedReply(
            f"Task proposal {pending.id}: {title}{due_line}\n\nNo task has been saved yet.",
            (ReplyAction("Accept task", f"/approve_action {pending.id}"), ReplyAction("Reject", f"/reject_action {pending.id}")),
        )


class StewardWorkspaceLinkApplication:
    """Create reviewable semantic workspace links without moving originals."""

    LINK_SOURCE = "link_source_to_workspace"

    def __init__(self, proposals: ActionProposalRepository, workspaces: WorkspaceRepository, sources: SourceRepository, activity: ActivityService) -> None:
        self._proposals = proposals
        self._workspaces = workspaces
        self._sources = sources
        self._activity = activity

    def handle_command(self, event: IncomingEvent) -> str | PresentedReply | None:
        command, separator, argument = (event.text or "").strip().partition(" ")
        command = command.partition("@")[0]
        if command != "/propose_link_source":
            return None
        parts = argument.split()
        if not separator or len(parts) != 2 or not all(part.isdigit() for part in parts):
            return "Use /propose_link_source followed by a workspace ID and source ID."
        workspace_id, source_id = (int(part) for part in parts)
        workspace = next((item for item in self._workspaces.list_all() if item.id == workspace_id), None)
        source = self._sources.get_by_id(source_id)
        if workspace is None:
            return f"Workspace {workspace_id} was not found."
        if source is None:
            return f"Source {source_id} was not found."
        payload = {"workspace_id": str(workspace_id), "source_id": str(source_id)}
        pending = self._proposals.find_pending(self.LINK_SOURCE, payload)
        if pending is None:
            pending = self._proposals.add(self.LINK_SOURCE, payload)
            self._activity.record(ActivityType.ACTION_PROPOSED, object_id=str(pending.id), details=f"Link source {source_id} to workspace {workspace_id}")
        return PresentedReply(
            f"Link proposal {pending.id}: relate source {source_id} ({source.path.name}) to workspace {workspace_id} ({workspace.name}).\n\nNo file will move.",
            (ReplyAction("Link", f"/approve_action {pending.id}"), ReplyAction("Reject", f"/reject_action {pending.id}")),
        )


class StewardIntegrationStatusApplication:
    """Report local integration readiness without reading OAuth secrets in chat."""

    def __init__(self, data_dir: Path) -> None:
        self._data_dir = data_dir

    def handle_command(self, event: IncomingEvent) -> str | None:
        command = (event.text or "").strip().partition(" ")[0].partition("@")[0]
        if command != "/integrations":
            return None
        configured = bool(os.environ.get("STEWARD_GOOGLE_CLIENT_SECRETS"))
        config_state = "configured locally" if configured else "client secrets not configured"
        token_dir = self._data_dir / "config"
        states = {
            "Calendar": token_dir / "google-calendar-token.json",
            "Drive": token_dir / "google-drive-token.json",
            "Gmail": token_dir / "gmail-token.json",
        }
        lines = ["Google integration status (metadata only):", f"OAuth client: {config_state}"]
        lines.extend(
            f"{name}: {'local token present' if token.is_file() else 'needs local browser authorization'}"
            for name, token in states.items()
        )
        lines.append("Authorize or change OAuth settings only on the local machine.")
        return "\n".join(lines)


class StewardResearchApplication:
    """Offer explicit, ephemeral web research and separately reviewed retention."""

    def __init__(
        self,
        provider_factory: Callable[[], ResearchProvider | None],
        retention: ResearchRetentionService,
    ) -> None:
        self._provider_factory = provider_factory
        self._retention = retention

    def handle_command(self, event: IncomingEvent) -> str | PresentedReply | None:
        command, separator, query = (event.text or "").strip().partition(" ")
        command = command.partition("@")[0]
        if command not in {"/research", "/research_retain"}:
            return None
        if not separator or not query.strip():
            return f"Use {command} followed by a research question."
        provider = self._provider_factory()
        if provider is None:
            return "External research is not configured for this Steward process."
        try:
            bundle = ResearchService(provider).research(query)
        except (ResearchProviderError, ValueError) as error:
            return f"External research is temporarily unavailable: {error}"
        if command == "/research_retain":
            result = self._retention.retain(bundle)
            state = "Already retained" if result.duplicate else "Retained"
            return f"{state} external research note in Inbox: {result.source.path}"
        sources = "\n".join(f"- {source.title}: {source.url}" for source in bundle.sources)
        text = f"External research — ephemeral, not saved:\n\n{bundle.answer}"
        if sources:
            text += f"\n\nExternal sources:\n{sources}"
        return PresentedReply(
            text,
            (ReplyAction("Keep as Inbox note", f"/research_retain {bundle.query}"),),
        )


class StewardCuratedNoteApplication:
    """Stage an explicitly supplied note before it becomes a canonical source."""

    CREATE_CURATED_NOTE = "create_curated_note"

    def __init__(self, proposals: ActionProposalRepository, activity: ActivityService) -> None:
        self._proposals = proposals
        self._activity = activity

    def handle_command(self, event: IncomingEvent) -> str | PresentedReply | None:
        command, separator, text = (event.text or "").strip().partition(" ")
        command = command.partition("@")[0]
        if command != "/propose_note":
            return None
        note = text.strip()
        if not separator or not note:
            return "Use /propose_note followed by the curated note you want to retain."
        payload = {"text": note}
        pending = self._proposals.find_pending(self.CREATE_CURATED_NOTE, payload)
        if pending is None:
            pending = self._proposals.add(self.CREATE_CURATED_NOTE, payload)
            self._activity.record(ActivityType.ACTION_PROPOSED, object_id=str(pending.id), details="Create curated Inbox note")
        preview = note if len(note) <= 500 else note[:497] + "..."
        return PresentedReply(
            f"Curated note proposal {pending.id}:\n{preview}\n\nIt has not been saved.",
            (ReplyAction("Save note", f"/approve_action {pending.id}"), ReplyAction("Discard", f"/reject_action {pending.id}")),
        )


class StewardKnowledgeApplication:
    """Expose canonical knowledge inspection and evidence proposals in Telegram."""

    def __init__(
        self,
        knowledge: KnowledgeService,
        fragments: SourceFragmentRepository,
        proposals: KnowledgeEnrichmentProposalRepository,
        activity: ActivityService,
    ) -> None:
        self._knowledge = knowledge
        self._fragments = fragments
        self._proposals = proposals
        self._activity = activity

    def handle_command(self, event: IncomingEvent) -> str | PresentedReply | None:
        command, separator, argument = (event.text or "").strip().partition(" ")
        command = command.partition("@")[0]
        if command == "/knowledge":
            if not separator or not argument.strip():
                return "Use /knowledge followed by a concept name or alias."
            concept = self._knowledge.find(argument.strip())
            if concept is None:
                return f"No canonical concept matches {argument.strip()!r}."
            claims = self._knowledge.list_claims(concept.id or 0)
            lines = [f"Concept {concept.id}: {concept.name}"]
            lines.extend(f"Claim {claim.id}: {claim.text}" for claim in claims)
            return "\n".join(lines)
        if command == "/knowledge_proposals":
            pending = [proposal for proposal in self._proposals.list_all() if proposal.status == "pending"]
            if not pending:
                return "No pending knowledge enrichment proposals."
            return "Pending knowledge proposals:\n" + "\n".join(
                f"{proposal.id}: {proposal.operation.value} claim {proposal.claim_id} "
                f"with fragment {proposal.fragment_id}" for proposal in pending
            )
        if command == "/propose_enrichment":
            parts = argument.split()
            if not separator or len(parts) != 2 or not all(part.isdigit() for part in parts):
                return "Use /propose_enrichment followed by a claim ID and fragment ID."
            claim = self._knowledge.get_claim(int(parts[0]))
            fragment = self._fragments.get(int(parts[1]))
            if claim is None:
                return f"Claim {parts[0]} was not found."
            if fragment is None:
                return f"Fragment {parts[1]} was not found."
            stored = self._proposals.add(
                self._knowledge.compare_evidence(claim, fragment_id=fragment.id or 0, evidence_text=fragment.text)
            )
            self._activity.record(
                ActivityType.KNOWLEDGE_ENRICHMENT_PROPOSED,
                object_id=str(stored.id), details=f"{stored.operation.value}: {stored.rationale}",
            )
            return PresentedReply(
                f"Knowledge proposal {stored.id}: {stored.operation.value.upper()} claim {stored.claim_id} "
                f"using fragment {stored.fragment_id}.\n{stored.rationale}",
                (
                    ReplyAction("Accept", f"/review_enrichment {stored.id} accepted"),
                    ReplyAction("Reject", f"/review_enrichment {stored.id} rejected"),
                ),
            )
        if command == "/review_enrichment":
            parts = argument.split()
            if not separator or len(parts) != 2 or not parts[0].isdigit() or parts[1] not in {"accepted", "rejected"}:
                return "Use /review_enrichment followed by a proposal ID and accepted or rejected."
            try:
                proposal = self._proposals.review(int(parts[0]), parts[1])
            except ValueError as error:
                return str(error)
            self._activity.record(
                ActivityType.KNOWLEDGE_ENRICHMENT_ACCEPTED if proposal.status == "accepted" else ActivityType.KNOWLEDGE_ENRICHMENT_REJECTED,
                object_id=str(proposal.id), details=f"{proposal.operation.value}: {proposal.rationale}",
            )
            return f"Knowledge enrichment proposal {proposal.id} {proposal.status}."
        return None


class StewardRootsApplication:
    """Report local source-root health without granting Telegram path authority."""

    def __init__(self, roots: SourceRootRepository) -> None:
        self._roots = roots

    def handle_command(self, event: IncomingEvent) -> str | None:
        command = (event.text or "").strip().partition(" ")[0].partition("@")[0]
        if command != "/roots":
            return None
        roots = self._roots.list_all()
        if not roots:
            return "No locally authorized source roots. Add one from the local CLI or setup UI."
        return "Authorized source roots:\n" + "\n".join(
            f"{root.id}: {root.name} — {root.health}"
            for root in roots
        )


class StewardPrivacyApplication:
    """Explicit source-level model-boundary controls for an authorized chat."""

    def __init__(self, privacy: PrivacyService, sources: SourceRepository) -> None:
        self._privacy = privacy
        self._sources = sources

    def handle_command(self, event: IncomingEvent) -> str | None:
        command, separator, argument = (event.text or "").strip().partition(" ")
        command = command.partition("@")[0]
        if command == "/privacy":
            if not separator or not argument.strip().isdigit():
                return "Use /privacy followed by a numeric source ID."
            source_id = int(argument.strip())
            if self._sources.get_by_id(source_id) is None:
                return f"Source {source_id} was not found."
            return f"Source {source_id} privacy rule: {self._privacy.rule_for(source_id).value}"
        if command != "/set_privacy":
            return None
        parts = argument.split()
        if not separator or len(parts) != 2 or not parts[0].isdigit():
            return "Use /set_privacy followed by a source ID and privacy rule."
        source_id = int(parts[0])
        if self._sources.get_by_id(source_id) is None:
            return f"Source {source_id} was not found."
        try:
            rule = PrivacyRule(parts[1])
        except ValueError:
            return "Privacy rule must be external_allowed, external_redacted, local_model_only, or no_model."
        self._privacy.set_rule(source_id, rule)
        return f"Source {source_id} privacy rule set to {rule.value}."


class StewardCalendarApplication:
    """Read current Calendar state through a lazy, locally authorized adapter."""

    def __init__(self, calendar_factory: Callable[[], CalendarService] | None) -> None:
        self._calendar_factory = calendar_factory

    def handle_command(self, event: IncomingEvent) -> str | None:
        command, separator, argument = (event.text or "").strip().partition(" ")
        command = command.partition("@")[0]
        if command not in {"/calendar_search", "/calendar_get"}:
            return None
        if self._calendar_factory is None:
            return "Calendar is not configured locally. Complete Calendar authorization on the local machine first."
        if command == "/calendar_get" and (not separator or not argument.strip()):
            return "Use /calendar_get followed by a Calendar event ID."
        try:
            calendar = self._calendar_factory()
            if command == "/calendar_get":
                event_result = calendar.get_event(argument.strip())
                return self._format_event(event_result.id, event_result.start, event_result.end, event_result.summary)
            events = calendar.search(argument.strip(), limit=10)
        except Exception as error:
            return f"Calendar is temporarily unavailable: {error}"
        if not events:
            return "No current Calendar events matched."
        return "Calendar events (current Google Calendar state):\n" + "\n".join(
            self._format_event(item.id, item.start, item.end, item.summary) for item in events
        )

    @staticmethod
    def _format_event(event_id: str, start: str, end: str, summary: str) -> str:
        return f"{event_id}: {start} → {end} — {summary}"


class StewardOperationsApplication:
    """Expose delivery diagnostics and reviewable recovery to the owner chat."""

    _LIMIT = 10
    RECOVER_DELIVERY = "recover_telegram_delivery"

    def __init__(
        self,
        deliveries: TelegramUpdateDeliveryRepository,
        proposals: ActionProposalRepository | None = None,
        activity: ActivityService | None = None,
    ) -> None:
        self._deliveries = deliveries
        self._proposals = proposals
        self._activity = activity

    def handle_command(self, event: IncomingEvent) -> str | None:
        command = (event.text or "").strip().partition(" ")[0].partition("@")[0]
        if command == "/deliveries":
            deliveries = self._deliveries.list_recent(limit=self._LIMIT)
            if not deliveries:
                return "No local Telegram delivery records."
            return "Recent Telegram deliveries (metadata only):\n" + "\n".join(
                f"{delivery.update_id}: {delivery.status}"
                f" (claimed {delivery.claimed_at.isoformat()})"
                for delivery in deliveries
            )
        if command == "/delivery_history":
            history = self._deliveries.list_history(limit=self._LIMIT)
            if not history:
                return "No local Telegram delivery history."
            return "Recent Telegram delivery history (metadata only):\n" + "\n".join(
                f"{entry.update_id}: {entry.event_type} ({entry.occurred_at.isoformat()})"
                for entry in history
            )
        if command == "/dead_letters":
            dead_letters = self._deliveries.list_dead_letters(limit=self._LIMIT)
            if not dead_letters:
                return "No terminal Telegram delivery failures."
            return "Telegram dead letters (metadata only; retry locally after review):\n" + "\n".join(
                f"{letter.update_id}: {letter.attempts} failed attempts ({letter.failed_at.isoformat()})"
                for letter in dead_letters
            )
        if command == "/recover_dead_letter":
            update_id = (event.text or "").strip().partition(" ")[2].strip()
            if not update_id or " " in update_id:
                return "Use /recover_dead_letter followed by one Telegram update ID."
            if self._proposals is None or self._activity is None:
                return "Telegram delivery recovery is not configured for this Steward process."
            dead_letter = self._deliveries.get_dead_letter(update_id)
            if dead_letter is None:
                return f"Telegram update {update_id!r} is not a dead letter."
            payload = {"update_id": update_id}
            pending = self._proposals.find_pending(self.RECOVER_DELIVERY, payload)
            if pending is None:
                pending = self._proposals.add(self.RECOVER_DELIVERY, payload)
                self._activity.record(
                    ActivityType.ACTION_PROPOSED,
                    object_id=str(pending.id),
                    details=f"Recover Telegram delivery retry budget: {update_id}",
                )
            return PresentedReply(
                f"Delivery recovery proposal {pending.id}: reopen {update_id} after "
                f"{dead_letter.attempts} failed attempt(s).\n\n"
                "This does not replay a message. It only allows a future genuine Telegram redelivery "
                "to receive a fresh retry budget.",
                (
                    ReplyAction("Reopen retry budget", f"/approve_action {pending.id}"),
                    ReplyAction("Keep terminal", f"/reject_action {pending.id}"),
                ),
            )
        return None


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
        if command == "/drive_search":
            return self._search(argument)
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

    def _search(self, query: str) -> str | PresentedReply:
        if self._importer is None or not hasattr(self._importer, "search"):
            return "Drive search is not configured on this Steward process. Authorize Drive locally first."
        try:
            results = self._importer.search(query.strip())
        except (OSError, ValueError) as error:
            return f"Drive search failed: {error}"
        if not results:
            return "No Drive files matched."
        visible = results[:5]
        return PresentedReply(
            "Drive files (metadata only):\n" + "\n".join(f"{item.id}: {item.name}" for item in visible),
            tuple(ReplyAction(f"Import {item.name[:32]}", f"/drive_import {item.id}") for item in visible),
        )


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
        if command == "/gmail_search":
            return self._search(argument)
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

    def _search(self, query: str) -> str | PresentedReply:
        if self._importer is None or not hasattr(self._importer, "search"):
            return "Gmail search is not configured on this Steward process. Authorize Gmail locally first."
        try:
            results = self._importer.search(query.strip())
        except (OSError, ValueError) as error:
            return f"Gmail search failed: {error}"
        if not results:
            return "No Gmail messages matched."
        visible = results[:5]
        return PresentedReply(
            "Gmail messages (metadata only):\n" + "\n".join(f"{item.id}: {item.subject}" for item in visible),
            tuple(ReplyAction(f"Import {item.subject[:32]}", f"/gmail_import {item.id}") for item in visible),
        )


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
        record_service: RecordService | None = None,
        fragment_repository: SourceFragmentRepository | None = None,
        activity_service: ActivityService | None = None,
        task_service: TaskService | None = None,
        capture_service: InboxCaptureService | None = None,
        workspace_repository: WorkspaceRepository | None = None,
        source_repository: SourceRepository | None = None,
        delivery_repository: TelegramUpdateDeliveryRepository | None = None,
    ) -> None:
        self._repository = repository
        self._service = service
        self._calendar_proposals = calendar_proposals
        self._calendar_writer_factory = calendar_writer_factory
        self._records = record_service
        self._fragments = fragment_repository
        self._activity = activity_service
        self._tasks = task_service
        self._capture = capture_service
        self._workspaces = workspace_repository
        self._sources = source_repository
        self._delivery_repository = delivery_repository

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
        if command in {"/calendar_travel", "/calendar_task"}:
            if not separator or not argument.strip().isdigit():
                target = "a saved travel record ID" if command == "/calendar_travel" else "a saved task ID"
                return f"Use {command} followed by {target}."
            if self._calendar_proposals is None:
                return "Calendar proposals are not configured on this Steward process."
            try:
                proposal = (
                    self._calendar_proposals.propose_travel_event(int(argument.strip()))
                    if command == "/calendar_travel"
                    else self._calendar_proposals.propose_task_event(int(argument.strip()))
                )
            except ValueError as error:
                return str(error)
            if command == "/calendar_task":
                return PresentedReply(
                    f"Calendar proposal {proposal.id} is pending for task {proposal.payload['task_id']}. "
                    "No Calendar event has been created.",
                    (
                        ReplyAction("Create deadline event", f"/approve_action {proposal.id}"),
                        ReplyAction("Reject", f"/reject_action {proposal.id}"),
                    ),
                )
            return PresentedReply(
                f"Calendar proposal {proposal.id} is pending for travel record "
                f"{proposal.payload['record_id']}. No Calendar event has been created.",
                (
                    ReplyAction("Create event", f"/approve_action {proposal.id}"),
                    ReplyAction("Reject", f"/reject_action {proposal.id}"),
                ),
            )
        if command == "/create_workspace":
            return self.propose_workspace(argument)
        if command not in {"/approve_action", "/reject_action"}:
            return None
        if not separator or not argument.strip().isdigit():
            return f"Use {command} followed by a numeric proposal ID."
        proposal_id = int(argument.strip())
        decision = "accepted" if command == "/approve_action" else "rejected"
        proposal = self._repository.get(proposal_id)
        if proposal is not None and proposal.action_type == StewardTaskApplication.CREATE_TASK:
            return self._review_task(proposal_id, decision)
        if proposal is not None and proposal.action_type == StewardOperationsApplication.RECOVER_DELIVERY:
            return self._review_delivery_recovery(proposal_id, decision)
        if proposal is not None and proposal.action_type == StewardCuratedNoteApplication.CREATE_CURATED_NOTE:
            return self._review_curated_note(proposal_id, decision, event)
        if proposal is not None and proposal.action_type == StewardWorkspaceLinkApplication.LINK_SOURCE:
            return self._review_workspace_link(proposal_id, decision)
        if proposal is not None and proposal.action_type in {
            StewardRecordApplication.CREATE_RECEIPT_RECORD,
            StewardRecordApplication.CREATE_WARRANTY_RECORD,
        }:
            return self._review_document_record(proposal_id, decision)
        if proposal is not None and proposal.action_type in {
            StewardRecordApplication.CORRECT_TRAVEL_RECORD,
            StewardRecordApplication.CORRECT_RECEIPT_RECORD,
            StewardRecordApplication.CORRECT_WARRANTY_RECORD,
        }:
            return self._review_record_correction(proposal_id, decision)
        if proposal is not None and proposal.action_type == StewardRecordApplication.CREATE_TRAVEL_RECORD:
            return self._review_travel_record(proposal_id, decision)
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

    def _review_task(self, proposal_id: int, decision: str) -> str:
        if self._tasks is None:
            return "Task creation is not configured for this Steward process."
        proposal = self._repository.get(proposal_id)
        if proposal is None:
            return "Task proposal was not found."
        if proposal.status == decision:
            return f"Task proposal {proposal.id} {proposal.status}."
        if proposal.status != "pending":
            return f"Task proposal {proposal.id} was already {proposal.status}."
        if decision == "rejected":
            self._repository.set_status(proposal_id, decision)
            if self._activity is not None:
                self._activity.record(ActivityType.ACTION_REJECTED, object_id=str(proposal_id), details=proposal.action_type)
            return f"Task proposal {proposal.id} rejected."
        due_at_value = proposal.payload.get("due_at") or None
        task = self._tasks.create(
            proposal.payload["title"],
            proposal.payload.get("due_hint") or None,
            TaskService.parse_due_at(due_at_value) if due_at_value else None,
        )
        self._repository.set_status(proposal_id, decision)
        if self._activity is not None:
            self._activity.record(ActivityType.TASK_CREATED, object_id=str(task.id), details=task.title)
            self._activity.record(ActivityType.ACTION_ACCEPTED, object_id=str(proposal_id), details=proposal.action_type)
        return f"Task {task.id} created: {task.title}."

    def _review_delivery_recovery(self, proposal_id: int, decision: str) -> str:
        if self._delivery_repository is None:
            return "Telegram delivery recovery is not configured for this Steward process."
        proposal = self._repository.get(proposal_id)
        if proposal is None:
            return "Delivery recovery proposal was not found."
        if proposal.status == decision:
            return f"Delivery recovery proposal {proposal.id} {proposal.status}."
        if proposal.status != "pending":
            return f"Delivery recovery proposal {proposal.id} was already {proposal.status}."
        if decision == "rejected":
            self._repository.set_status(proposal_id, decision)
            if self._activity is not None:
                self._activity.record(ActivityType.ACTION_REJECTED, object_id=str(proposal_id), details=proposal.action_type)
            return f"Delivery recovery proposal {proposal.id} rejected."
        update_id = proposal.payload["update_id"]
        try:
            self._delivery_repository.reopen_dead_letter(update_id)
        except ValueError as error:
            return str(error)
        self._repository.set_status(proposal_id, decision)
        if self._activity is not None:
            self._activity.record(ActivityType.TELEGRAM_DELIVERY_RECOVERED, object_id=update_id, details="Approved Telegram recovery; no message was replayed.")
            self._activity.record(ActivityType.ACTION_ACCEPTED, object_id=str(proposal_id), details=proposal.action_type)
        return f"Reopened {update_id} for a future genuine Telegram redelivery. No message was replayed."

    def _review_curated_note(self, proposal_id: int, decision: str, event: IncomingEvent) -> str:
        if self._capture is None:
            return "Curated-note capture is not configured for this Steward process."
        proposal = self._repository.get(proposal_id)
        if proposal is None:
            return "Curated note proposal was not found."
        if proposal.status == decision:
            return f"Curated note proposal {proposal.id} {proposal.status}."
        if proposal.status != "pending":
            return f"Curated note proposal {proposal.id} was already {proposal.status}."
        if decision == "rejected":
            self._repository.set_status(proposal_id, decision)
            if self._activity is not None:
                self._activity.record(ActivityType.ACTION_REJECTED, object_id=str(proposal_id), details=proposal.action_type)
            return f"Curated note proposal {proposal.id} rejected."
        result = self._capture.capture_text(
            IncomingEvent(
                id=f"curated-note:{proposal_id}", platform="curated_note", chat_id=event.chat_id,
                message_id=str(proposal_id), reply_to_id=event.reply_to_id, timestamp=event.timestamp,
                text=proposal.payload["text"], attachments=(),
            )
        )
        self._repository.set_status(proposal_id, decision)
        if self._activity is not None:
            self._activity.record(ActivityType.ACTION_ACCEPTED, object_id=str(proposal_id), details=proposal.action_type)
        state = "Already saved" if result.duplicate else "Saved"
        return f"{state} curated note to Inbox: {result.source.path}"

    def _review_workspace_link(self, proposal_id: int, decision: str) -> str:
        if self._workspaces is None or self._sources is None:
            return "Workspace linking is not configured for this Steward process."
        proposal = self._repository.get(proposal_id)
        if proposal is None:
            return "Link proposal was not found."
        if proposal.status == decision:
            return f"Link proposal {proposal.id} {proposal.status}."
        if proposal.status != "pending":
            return f"Link proposal {proposal.id} was already {proposal.status}."
        if decision == "rejected":
            self._repository.set_status(proposal_id, decision)
            if self._activity is not None:
                self._activity.record(ActivityType.ACTION_REJECTED, object_id=str(proposal_id), details=proposal.action_type)
            return f"Link proposal {proposal.id} rejected."
        workspace_id = int(proposal.payload["workspace_id"])
        source_id = int(proposal.payload["source_id"])
        if not any(item.id == workspace_id for item in self._workspaces.list_all()) or self._sources.get_by_id(source_id) is None:
            return "The workspace or source is no longer available; the link was not created."
        self._workspaces.link_source(workspace_id, source_id)
        self._repository.set_status(proposal_id, decision)
        if self._activity is not None:
            self._activity.record(ActivityType.SOURCE_LINKED_TO_WORKSPACE, object_id=str(source_id), details=f"workspace:{workspace_id}")
            self._activity.record(ActivityType.ACTION_ACCEPTED, object_id=str(proposal_id), details=proposal.action_type)
        return f"Source {source_id} linked to workspace {workspace_id}. No file moved."

    def _review_travel_record(self, proposal_id: int, decision: str) -> str:
        if self._records is None or self._fragments is None:
            return "Travel-record review is not configured on this Steward process."
        proposal = self._repository.get(proposal_id)
        if proposal is None:
            return "Action proposal was not found."
        if proposal.status == decision:
            return f"Travel-record proposal {proposal.id} {proposal.status}."
        if proposal.status != "pending":
            return f"Action proposal {proposal.id} was already {proposal.status}."
        if decision == "accepted":
            source_id = int(proposal.payload["source_id"])
            fragments = self._fragments.list_for_source(source_id)
            record_proposal = self._records.propose_travel_record(
                source_id, [(fragment.id or 0, fragment.text) for fragment in fragments]
            )
            try:
                record = self._records.create_from_proposal(record_proposal)
            except ValueError as error:
                return str(error)
            self._repository.set_status(proposal_id, decision)
            if self._activity is not None:
                self._activity.record(ActivityType.ACTION_ACCEPTED, object_id=str(proposal_id), details=proposal.action_type)
            return f"Travel record {record.id} created from source {source_id}."
        self._repository.set_status(proposal_id, decision)
        if self._activity is not None:
            self._activity.record(ActivityType.ACTION_REJECTED, object_id=str(proposal_id), details=proposal.action_type)
        return f"Travel-record proposal {proposal.id} rejected."

    def _review_document_record(self, proposal_id: int, decision: str) -> str:
        if self._records is None or self._fragments is None:
            return "Record review is not configured on this Steward process."
        proposal = self._repository.get(proposal_id)
        if proposal is None:
            return "Action proposal was not found."
        label = "receipt" if proposal.action_type == StewardRecordApplication.CREATE_RECEIPT_RECORD else "warranty"
        if proposal.status == decision:
            return f"{label.title()} proposal {proposal.id} {proposal.status}."
        if proposal.status != "pending":
            return f"{label.title()} proposal {proposal.id} was already {proposal.status}."
        if decision == "rejected":
            self._repository.set_status(proposal_id, decision)
            if self._activity is not None:
                self._activity.record(ActivityType.ACTION_REJECTED, object_id=str(proposal_id), details=proposal.action_type)
            return f"{label.title()} proposal {proposal.id} rejected."
        source_id = int(proposal.payload["source_id"])
        fragments = [(item.id or 0, item.text) for item in self._fragments.list_for_source(source_id)]
        extracted = (
            self._records.propose_receipt_record(source_id, fragments)
            if label == "receipt" else self._records.propose_warranty_record(source_id, fragments)
        )
        try:
            record = (
                self._records.create_receipt_from_proposal(extracted)
                if label == "receipt" else self._records.create_warranty_from_proposal(extracted)
            )
        except ValueError as error:
            return str(error)
        self._repository.set_status(proposal_id, decision)
        if self._activity is not None:
            self._activity.record(ActivityType.ACTION_ACCEPTED, object_id=str(proposal_id), details=proposal.action_type)
        return f"{label.title()} record {record.id} created from source {source_id}."

    def _review_record_correction(self, proposal_id: int, decision: str) -> str:
        if self._records is None:
            return "Record correction is not configured for this Steward process."
        proposal = self._repository.get(proposal_id)
        if proposal is None:
            return "Record correction proposal was not found."
        if proposal.status == decision:
            return f"Travel correction proposal {proposal.id} {proposal.status}."
        if proposal.status != "pending":
            return f"Travel correction proposal {proposal.id} was already {proposal.status}."
        if decision == "rejected":
            self._repository.set_status(proposal_id, decision)
            return f"Record correction proposal {proposal.id} rejected."
        label, correct = {
            StewardRecordApplication.CORRECT_TRAVEL_RECORD: ("Travel", self._records.correct_travel_field),
            StewardRecordApplication.CORRECT_RECEIPT_RECORD: ("Receipt", self._records.correct_receipt_field),
            StewardRecordApplication.CORRECT_WARRANTY_RECORD: ("Warranty", self._records.correct_warranty_field),
        }[proposal.action_type]
        try:
            record = correct(int(proposal.payload["record_id"]), proposal.payload["field"], proposal.payload["value"])
        except ValueError as error:
            return str(error)
        self._repository.set_status(proposal_id, decision)
        if self._activity is not None:
            self._activity.record(ActivityType.RECORD_CORRECTED, object_id=str(record.id), details=f"{label.casefold()}:{proposal.payload['field']}")
            self._activity.record(ActivityType.ACTION_ACCEPTED, object_id=str(proposal_id), details=proposal.action_type)
        return f"{label} record {record.id} corrected: {proposal.payload['field']}."

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
        record_application: StewardRecordApplication | None = None,
        task_application: StewardTaskApplication | None = None,
        research_application: StewardResearchApplication | None = None,
        curated_note_application: StewardCuratedNoteApplication | None = None,
        workspace_link_application: StewardWorkspaceLinkApplication | None = None,
        integration_status_application: StewardIntegrationStatusApplication | None = None,
        knowledge_application: StewardKnowledgeApplication | None = None,
        roots_application: StewardRootsApplication | None = None,
        privacy_application: StewardPrivacyApplication | None = None,
        operations_application: StewardOperationsApplication | None = None,
        calendar_application: StewardCalendarApplication | None = None,
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
        self._record_application = record_application
        self._task_application = task_application
        self._research_application = research_application
        self._curated_note_application = curated_note_application
        self._workspace_link_application = workspace_link_application
        self._integration_status_application = integration_status_application
        self._knowledge_application = knowledge_application
        self._roots_application = roots_application
        self._privacy_application = privacy_application
        self._operations_application = operations_application
        self._calendar_application = calendar_application

    def handle(self, event: IncomingEvent) -> str | PresentedReply:
        if self._task_application is not None:
            normalized = (event.text or "").strip().casefold()
            if normalized.startswith(("remind me to ", "todo:", "task:", "deadline:")):
                return self._task_application.propose(event.text or "")
        if self._calendar_application is not None:
            calendar_response = self._calendar_application.handle_command(event)
            if calendar_response is not None:
                return calendar_response
        if self._operations_application is not None:
            operations_response = self._operations_application.handle_command(event)
            if operations_response is not None:
                return operations_response
        if self._privacy_application is not None:
            privacy_response = self._privacy_application.handle_command(event)
            if privacy_response is not None:
                return privacy_response
        if self._roots_application is not None:
            roots_response = self._roots_application.handle_command(event)
            if roots_response is not None:
                return roots_response
        if self._knowledge_application is not None:
            knowledge_response = self._knowledge_application.handle_command(event)
            if knowledge_response is not None:
                return knowledge_response
        if self._record_application is not None:
            record_response = self._record_application.handle_command(event)
            if record_response is not None:
                return record_response
        if self._task_application is not None:
            task_response = self._task_application.handle_command(event)
            if task_response is not None:
                return task_response
        if self._research_application is not None:
            research_response = self._research_application.handle_command(event)
            if research_response is not None:
                return research_response
        if self._curated_note_application is not None:
            note_response = self._curated_note_application.handle_command(event)
            if note_response is not None:
                return note_response
        if self._workspace_link_application is not None:
            link_response = self._workspace_link_application.handle_command(event)
            if link_response is not None:
                return link_response
        if self._integration_status_application is not None:
            integration_response = self._integration_status_application.handle_command(event)
            if integration_response is not None:
                return integration_response
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
