"""Application-level use cases composed from Steward domain services."""

from __future__ import annotations

import os
import secrets
from datetime import UTC, datetime, timedelta
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
from steward.sources.service import SourceService
from steward.workspaces import Workspace
from steward.workspaces import WorkspaceRepository
from steward.activity import ActivityService, ActivityType
from steward.action_proposals import ActionProposalRepository, ActionProposalService
from steward.calendar import CalendarEventProposalService, CalendarService, CalendarWriteService
from steward.extraction import InvalidSearchQueryError, SourceFragmentRepository
from steward.retrieval import HybridRetriever, LexicalSearchService, SemanticSearchService
from steward.sources import SourceRepository
from steward.presentation import PresentedReply, ReplyAction
from steward.intake import (
    IntakeAnalysisMode,
    ProvisionalIntake,
    ProvisionalIntakeRepository,
    ProvisionalIntakeService,
)
from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.errors import GraphRecursionError
from steward.answer.gateway import ModelGateway, ModelGatewayError
from steward.records import RecordService
from steward.knowledge import (
    EnrichmentOperation,
    KnowledgeEnrichmentProposalRepository,
    KnowledgeService,
    StoredKnowledgeEnrichmentProposal,
)
from steward.knowledge_connector import KnowledgeConnector
from steward.roots import SourceRootRepository
from steward.privacy import PrivacyRule, PrivacyService
from steward.telegram import TelegramUpdateDeliveryRepository
from steward.tasks import TaskReminderService, TaskService
from steward.research import ResearchBundle, ResearchProvider, ResearchProviderError, ResearchRetentionService, ResearchService
from steward.reviews import ReviewContextRepository


TEXT_QUESTION_REQUIRED = "Send a text question and I will search your local knowledge."


class StewardReviewInboxApplication:
    """Adapt separate proposal domains into one human-facing Telegram review inbox.

    This is deliberately a presentation/use-case adapter, not a replacement for
    the underlying proposal repositories. Each existing service still owns the
    validation and execution of its own decision.
    """

    _MAX_ITEMS = 8

    def __init__(
        self,
        action_proposals: ActionProposalRepository,
        organization_proposals: OrganizationProposalRepository,
        sources: SourceRepository,
        *,
        intakes: ProvisionalIntakeRepository | None = None,
        knowledge_proposals: KnowledgeEnrichmentProposalRepository | None = None,
        contexts: ReviewContextRepository | None = None,
    ) -> None:
        self._actions = action_proposals
        self._organizations = organization_proposals
        self._sources = sources
        self._intakes = intakes
        self._knowledge = knowledge_proposals
        self._contexts = contexts

    def handle_command(self, event: IncomingEvent) -> str | PresentedReply | None:
        command, _, argument = (event.text or "").strip().partition(" ")
        command = command.partition("@")[0].casefold()
        if command in {"/home", "/pending"}:
            return self.pending(event)
        if command != "/review":
            return None
        kind, separator, identifier = argument.partition(" ")
        if not separator or not identifier.isdigit():
            return "Choose a review from /pending."
        return self.detail(event, kind.casefold(), int(identifier))

    def pending(self, event: IncomingEvent) -> PresentedReply:
        """Show only items this chat can safely act on, with no internal payloads."""

        items = self._pending_items(event)[: self._MAX_ITEMS]
        if not items:
            return PresentedReply(
                "There is nothing waiting for your decision.",
                (ReplyAction("Search", "/search "), ReplyAction("Inbox", "/inbox")),
                title="All caught up",
                icon="✅",
            )
        lines = ["Choose an item to see what will change before deciding."]
        actions: list[ReplyAction] = []
        for index, (kind, identifier, summary) in enumerate(items, start=1):
            lines.append(f"{index}. {summary}")
            actions.append(ReplyAction(f"Review {index}", f"/review {kind} {identifier}"))
        return PresentedReply(
            "\n".join(lines), tuple(actions), title=f"{len(items)} decision{'s' if len(items) != 1 else ''} waiting", icon="🕒"
        )

    def detail(self, event: IncomingEvent, kind: str, identifier: int) -> str | PresentedReply:
        """Render one complete, human-readable proposal card."""

        if self._contexts is not None:
            self._contexts.set(event.platform, event.chat_id, kind, identifier)

        if kind == "action":
            proposal = self._actions.get(identifier)
            if proposal is None or proposal.status != "pending":
                return "That review is no longer waiting for a decision. Send /pending for the current list."
            title, description = self._action_summary(proposal.action_type, proposal.payload)
            return PresentedReply(
                f"{description}\n\nNo change has been made yet.",
                (ReplyAction("Accept", f"/approve_action {identifier}"), ReplyAction("Reject", f"/reject_action {identifier}")),
                title=title,
                icon="⚠️",
            )
        if kind == "organization":
            proposal = self._organizations.get(identifier)
            if proposal is None or proposal.status != "pending":
                return "That organization decision is no longer waiting. Send /pending for the current list."
            source = self._sources.get_by_id(proposal.source_id)
            filename = source.path.name if source is not None else "the saved source"
            target = proposal.workspace_name or (
                f"workspace {proposal.workspace_id}" if proposal.workspace_id is not None else "Inbox"
            )
            effect = "The original remains in Inbox." if proposal.suggested_path is None else f"The original will move to {target}."
            return PresentedReply(
                f"Suggested destination: {target}\nWhy: {proposal.rationale}\nEffect: {effect}",
                (
                    ReplyAction("Accept", f"/organization_accept {identifier}"),
                    ReplyAction("Inbox", f"/organization_keep_inbox {identifier}"),
                    ReplyAction("Reject", f"/organization_reject {identifier}"),
                ),
                title=f"Organize {filename}", icon="📁"
            )
        if kind == "intake" and self._intakes is not None:
            intake = self._intakes.get(identifier)
            if intake is None or intake.status != "pending" or intake.chat_id != event.chat_id:
                return "That staged item is no longer waiting in this chat. Send /pending for the current list."
            return PresentedReply(
                f"Type: {intake.category}\nSummary: {intake.summary}\nAnalysis: {intake.analysis_mode.value}\n\nIt is staged locally and has not been saved.",
                (
                    ReplyAction("Save", f"/intake_accept {identifier}"),
                    ReplyAction("Use local", f"/intake_analysis {identifier} local"),
                    ReplyAction("Use external", f"/intake_analysis {identifier} external"),
                    ReplyAction("Discard", f"/intake_discard {identifier}"),
                ),
                title=f"Review {intake.original_name}", icon="📄"
            )
        if kind == "knowledge" and self._knowledge is not None:
            proposal = self._knowledge.get(identifier)
            if proposal is None or proposal.status != "pending":
                return "That knowledge review is no longer waiting. Send /pending for the current list."
            return PresentedReply(
                f"Suggested change: {proposal.operation.value}\nWhy: {proposal.rationale}\nEvidence fragment: {proposal.fragment_id}",
                (
                    ReplyAction("Accept", f"/review_enrichment {identifier} accepted"),
                    ReplyAction("Reject", f"/review_enrichment {identifier} rejected"),
                ),
                title="Knowledge update", icon="🧠"
            )
        return "That review type is unavailable. Send /pending for the current list."

    def handle_followup(self, event: IncomingEvent) -> str | PresentedReply | None:
        """Resolve an explanatory follow-up to the last explicitly opened review card."""

        if self._contexts is None:
            return None
        text = (event.text or "").strip().casefold().rstrip("?!.")
        if text not in {"what is this", "what is this proposal", "why", "details", "show details"}:
            return None
        context = self._contexts.get(event.platform, event.chat_id)
        if context is None:
            return None
        return self.detail(event, context.kind, context.identifier)

    def handle_natural_request(self, event: IncomingEvent) -> PresentedReply | None:
        """Keep common review-list requests out of the general retrieval agent."""

        text = (event.text or "").strip().casefold().rstrip("?!.")
        requests = {
            "what are the proposals",
            "what proposals are there",
            "show my proposals",
            "show pending proposals",
            "show pending reviews",
            "what needs my attention",
            "what is pending",
            "what do i need to decide",
        }
        return self.pending(event) if text in requests else None

    def _pending_items(self, event: IncomingEvent) -> list[tuple[str, int, str]]:
        items: list[tuple[str, int, str]] = []
        for proposal in reversed(self._actions.list_all()):
            if proposal.status == "pending" and proposal.id is not None:
                title, _ = self._action_summary(proposal.action_type, proposal.payload)
                items.append(("action", proposal.id, title))
        for proposal in reversed(self._organizations.list_all()):
            if proposal.status == "pending" and proposal.id is not None:
                source = self._sources.get_by_id(proposal.source_id)
                filename = source.path.name if source is not None else "saved source"
                items.append(("organization", proposal.id, f"Organize {filename}"))
        if self._intakes is not None:
            for intake in reversed(self._intakes.list_all()):
                if intake.status == "pending" and intake.chat_id == event.chat_id and intake.id is not None:
                    items.append(("intake", intake.id, f"Save {intake.original_name}"))
        if self._knowledge is not None:
            for proposal in reversed(self._knowledge.list_all()):
                if proposal.status == "pending":
                    items.append(("knowledge", proposal.id, "Review a knowledge update"))
        return items

    @staticmethod
    def _action_summary(action_type: str, payload: dict[str, str]) -> tuple[str, str]:
        """Translate known internal action types without exposing arbitrary payload JSON."""

        if action_type == ActionProposalService.CREATE_WORKSPACE:
            name = payload.get("name", "new workspace")
            return f"Create workspace {name}", f"A new workspace named {name} will be created."
        if action_type == StewardTaskApplication.CREATE_TASK:
            title = payload.get("title", "task")
            due = payload.get("due_at") or payload.get("due_hint")
            return f"Save task: {title}", f"This task will be saved." + (f" Due: {due}." if due else "")
        if action_type.startswith("create_calendar"):
            return "Create calendar event", "A new event will be added to Google Calendar after approval."
        if action_type.startswith("create_") and "record" in action_type:
            return "Save extracted record", "A record will be created from the reviewed source evidence."
        if action_type.startswith("correct_"):
            return "Apply record correction", "The reviewed record fields will be corrected from source evidence."
        if action_type == "unregister_source":
            return "Remove source metadata", "Steward will remove local metadata; the original file will remain untouched."
        if action_type == "reextract_source":
            return "Refresh extracted text", "Derived text and search fragments will be rebuilt; the original stays unchanged."
        if action_type == "rebuild_semantic_index":
            return "Rebuild local search index", "Derived semantic search data will be rebuilt; original sources stay unchanged."
        return "Review requested action", "Steward needs your approval before making this change."


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
        semantic_search: SemanticSearchService | None = None,
        hybrid_retriever: HybridRetriever | None = None,
        runtime_status: Callable[[], tuple[str, ...]] | None = None,
    ) -> None:
        self._sources = source_repository
        self._fragments = fragment_repository
        self._lexical = lexical_search
        self._workspaces = workspace_repository
        self._activity = activity_service
        self._inbox_dir = inbox_dir.resolve()
        self._action_proposals = action_proposals
        self._deliveries = deliveries
        self._semantic_search = semantic_search
        self._hybrid_retriever = hybrid_retriever
        self._runtime_status = runtime_status

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
        if command == "/metrics":
            return self.metrics()
        if command == "/search":
            return self.search(argument)
        if command == "/semantic_search":
            return self.semantic_search(argument)
        if command == "/hybrid_search":
            return self.hybrid_search(argument)
        return None

    @staticmethod
    def help_text() -> str:
        return (
            "Steward Telegram guide\n\n"
            "Start here:\n"
            "/home or /pending — review items that need your decision\n"
            "/search QUESTION — search your saved material\n"
            "/calendar_search [terms] — check your Calendar\n"
            "/workspaces — see ongoing contexts\n\n"
            "Read-only:\n"
            "/status — local service summary\n"
            "/inbox [page] — saved Inbox sources\n"
            "/sources [page] — registered sources\n"
            "/source ID — one source and its extracted-text status\n"
            "/search TERMS — local lexical search\n"
            "/semantic_search QUESTION — local meaning-based search\n"
            "/hybrid_search QUESTION — combined lexical and semantic search\n"
            "/workspaces — current workspaces\n"
            "/activity [term] — recent audit events\n"
            "/records, /tasks, /roots — saved state\n"
            "/calendar_search [terms], /calendar_get ID — current Google Calendar\n\n"
            "/metrics - aggregate local decisions and operational events\n"
            "Explicit actions (they create a review or a selected import):\n"
            "/action_proposals â€” pending action reviews\n"
            "/propose_task TEXT [--remind-at ISO_TIMESTAMP], /complete_task ID\n"
            "Natural task capture: `remind me to â€¦`, `todo: â€¦`, `task: â€¦`, or `deadline: â€¦`\n"
            "/propose_note TEXT, /curate (reply to a discussion message), /curate_synthesize [local|external], /research QUESTION\n"
            "/knowledge NAME, /connect_knowledge, /knowledge_proposal ID — inspect evidence-backed concepts\n"
            "For a staged attachment/note: /intake_analysis ID external|local|none, /intake_context ID TEXT, /intake_accept ID, /intake_discard ID\n"
            "/propose_travel_record SOURCE_ID\n"
            "/propose_receipt_record SOURCE_ID\n"
            "/propose_warranty_record SOURCE_ID\n"
            "/travel_references RECORD_ID, /propose_travel_reference RECORD_ID TYPE FRAGMENT_ID VALUE\n"
            "/correct_travel_record RECORD_ID FIELD VALUE\n"
            "/correct_receipt_record RECORD_ID FIELD VALUE\n"
            "/correct_warranty_record RECORD_ID FIELD VALUE\n"
            "/calendar_travel RECORD_ID, /calendar_task TASK_ID\n"
            "/drive_search QUERY, /gmail_search QUERY (then select one import)\n"
            "/privacy SOURCE_ID, /set_privacy SOURCE_ID RULE\n\n"
            "/propose_reextract SOURCE_ID, /propose_rebuild_index, /propose_unregister_source SOURCE_ID â€” reviewed source maintenance\n\n"
            "Review-required writes use the buttons or /approve_action ID and "
            "/reject_action ID. /save remains an explicit immediate Inbox shortcut.\n\n"
            "Inspect organization history with /organization_proposals. Refine a pending organization proposal with /organization_context ID EXISTING_WORKSPACE, "
            "or /organization_keep_inbox ID.\n\n"
            "Admin diagnostics: /integrations, /deliveries, /delivery_history, /dead_letters\n"
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
        if self._runtime_status is not None:
            try:
                lines.extend(self._runtime_status())
            except Exception:
                lines.append("Local runtime health: temporarily unavailable")
        lines.append("Use /help for available Telegram interactions.")
        return "\n".join(lines)

    def inbox(self, page: int) -> str | PresentedReply:
        sources = [source for source in self._sources.list_active() if self._is_inbox(source.path)]
        return self._source_list("Inbox", sources, page)

    def sources(self, page: int) -> str | PresentedReply:
        return self._source_list("Registered sources", self._sources.list_all(), page)

    def source(self, argument: str) -> str | PresentedReply:
        try:
            source_id = int(argument)
        except ValueError:
            return "Use /source followed by a numeric source ID."
        source = self._sources.get_by_id(source_id)
        if source is None:
            return f"Source {source_id} was not found."
        fragments = self._fragments.list_for_source(source_id)
        return PresentedReply(
            f"Type: {source.source_type.value}\nStatus: {source.status.value}\n"
            f"Extracted sections: {len(fragments)}",
            title=source.path.name,
            icon="📄",
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
            f"{event.id}: {event.event_type.value} — {self._safe_activity_details(event.details)}"
            for event in events
        )

    def metrics(self) -> str:
        """Show aggregate operational evidence without exposing event content."""
        counts = self._activity.counts()
        if not counts:
            return "No local activity metrics yet."
        return "Local activity metrics:\n" + "\n".join(
            f"{event_type.value}: {count}"
            for event_type, count in sorted(counts.items(), key=lambda item: item[0].value)
        )

    @staticmethod
    def _safe_activity_details(details: str) -> str:
        """Present file-related audit details without exposing local paths.

        The audit database retains the original value locally so that recovery
        and operator inspection remain useful. Telegram is an external
        transport, however, so it receives only the final filename.
        """
        if not details:
            return "no details"
        looks_like_path = (
            details.startswith("/")
            or (len(details) > 2 and details[1] == ":" and details[2] in "\\/")
            or "/" in details
            or "\\" in details
        )
        if not looks_like_path:
            return details
        filename = details.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]
        return f"local file: {filename or '(name withheld)'}"

    def search(self, query: str) -> str | PresentedReply:
        if not query:
            return "Use /search followed by one or more terms."
        try:
            hits = self._lexical.search(query, limit=5)
        except InvalidSearchQueryError:
            return "Those search terms are not valid. Try plain words without search operators."
        if not hits:
            return f"No local source fragments matched: {query!r}."
        return self._search_card("Search results", query, hits)

    def semantic_search(self, query: str) -> str | PresentedReply:
        """Search already-derived local vectors without involving an LLM."""
        if not query:
            return "Use /semantic_search followed by a natural-language phrase."
        if self._semantic_search is None:
            return "Semantic search is not configured locally. Install the local embedding model first."
        try:
            hits = self._semantic_search.search(query, limit=5)
        except (OSError, RuntimeError, ValueError):
            return "Semantic search is temporarily unavailable. Verify the local embedding model and derived index."
        if not hits:
            return f"No local semantic matches: {query!r}."
        return self._search_card("Semantic search results", query, hits, score_label="Similarity")

    def hybrid_search(self, query: str) -> str | PresentedReply:
        """Fuse local lexical and semantic rankings without a provider call."""
        if not query:
            return "Use /hybrid_search followed by a question or phrase."
        if self._hybrid_retriever is None:
            return "Hybrid search is not configured locally. Install the local embedding model first."
        try:
            hits = self._hybrid_retriever.search(query, limit=5)
        except (OSError, RuntimeError, ValueError):
            return "Hybrid search is temporarily unavailable. Verify the local embedding model and derived index."
        if not hits:
            return f"No local hybrid matches: {query!r}."
        return self._search_card("Hybrid search results", query, hits, score_label="Match")

    @staticmethod
    def _search_card(
        title: str, query: str, hits: object, *, score_label: str | None = None
    ) -> PresentedReply:
        """Render local retrieval as compact source cards with opaque callbacks."""

        lines = [f"Results for: {query}"]
        actions: list[ReplyAction] = []
        for index, hit in enumerate(hits, start=1):  # type: ignore[union-attr]
            heading = hit.fragment.heading or "Preamble"
            excerpt = (getattr(hit, "highlighted_text", None) or hit.fragment.text).replace("\n", " ").strip()
            score = f" · {score_label}: {hit.score:.2f}" if score_label else ""
            lines.append(
                f"\n{index}. {hit.source.path.name}\n{heading} · {hit.fragment.location}{score}\n“{excerpt[:180]}”"
            )
            if hit.source.id is not None:
                actions.append(ReplyAction(f"Open {index}", f"/source {hit.source.id}"))
        return PresentedReply("\n".join(lines), tuple(actions), title=title, icon="🔎")

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
        lines = [f"Page {page} of {pages}"]
        lines.extend(
            f"{index}. {source.path.name} · {source.source_type.value} · {source.status.value}"
            for index, source in enumerate(selected, start=1)
        )
        command = "/inbox" if title == "Inbox" else "/sources"
        actions: list[ReplyAction] = []
        actions.extend(
            ReplyAction(f"Open {index}", f"/source {source.id}")
            for index, source in enumerate(selected, start=1)
            if source.id is not None
        )
        if page > 1:
            actions.append(ReplyAction("Back", f"{command} {page - 1}"))
        if page < pages:
            actions.append(ReplyAction("Next", f"{command} {page + 1}"))
        return PresentedReply("\n".join(lines), tuple(actions), title=title, icon="📚")

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
    """Expose an allowlisted ToolNode loop for ordinary Telegram questions."""

    def __init__(self, graph: ToolAgentGraph) -> None:
        self._graph = graph

    def handle_command(self, event: IncomingEvent) -> str | PresentedReply | None:
        command, separator, question = (event.text or "").strip().partition(" ")
        command = command.partition("@")[0]
        if command != "/agent":
            return None
        if not separator or not question.strip():
            return "Use /agent followed by a question that may need Steward's read-only tools."
        return self._ask(question.strip(), event)

    def handle_request(self, event: IncomingEvent) -> str | PresentedReply | None:
        """Use tools for normal language, but never reinterpret explicit commands."""

        question = (event.text or "").strip()
        if not question or question.startswith("/"):
            return None
        return self._ask(question, event)

    def _ask(self, question: str, event: IncomingEvent) -> str | PresentedReply:
        try:
            result = self._graph.invoke(
                {
                    "messages": [
                        SystemMessage(
                            "You are Steward. Use only supplied allowlisted read-only tools when needed. "
                            "Never claim a tool result you did not receive; answer as soon as the result is sufficient."
                        ),
                        HumanMessage(question),
                    ]
                },
                {"configurable": {"thread_id": f"tool-agent:{event.platform}:{event.chat_id}"}, "recursion_limit": 16},
            )
        except GraphRecursionError:
            return (
                "I stopped the tool workflow before it could loop further. "
                "No write was performed; please narrow the request and try again."
            )
        except ModelGatewayError:
            return "The configured model is temporarily unavailable. Please retry later or use a local model."
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
    ADD_TRAVEL_REFERENCE = "add_travel_record_reference"

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

    def handle_command(self, event: IncomingEvent) -> str | PresentedReply | None:
        command, separator, argument = (event.text or "").strip().partition(" ")
        command = command.partition("@")[0]
        if command == "/records":
            return self._list_records()
        if command == "/travel_references":
            return self._travel_references(separator, argument)
        if command == "/propose_travel_reference":
            return self._propose_travel_reference(separator, argument)
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

    def _travel_references(self, separator: str, argument: str) -> str:
        if not separator or not argument.strip().isdigit():
            return "Use /travel_references followed by a numeric travel record ID."
        record_id = int(argument.strip())
        if self._records.get_travel_record(record_id) is None:
            return f"Travel record {record_id} was not found."
        references = self._records.list_references(record_id)
        if not references:
            return f"Travel record {record_id} has no additional source-backed references."
        return f"Travel record {record_id} references:\n" + "\n".join(
            f"{reference.id}: {reference.reference_type} = {reference.value} (fragment {reference.fragment_id})"
            for reference in references
        )

    def _propose_travel_reference(self, separator: str, argument: str) -> str | PresentedReply:
        """Stage an evidence-grounded reference rather than writing from chat."""
        parts = argument.split(maxsplit=3)
        if not separator or len(parts) != 4 or not parts[0].isdigit() or not parts[2].isdigit():
            return (
                "Use /propose_travel_reference followed by record ID, reference type, "
                "fragment ID, and the exact value from that fragment."
            )
        record_id, reference_type, fragment_id_text, value = parts
        fragment_id = int(fragment_id_text)
        record = self._records.get_travel_record(int(record_id))
        if record is None:
            return f"Travel record {record_id} was not found."
        fragment = self._fragments.get(fragment_id)
        if fragment is None:
            return f"Fragment {fragment_id} was not found."
        if fragment.source_id != record.source_id:
            return "Reference evidence must belong to the travel record's source."
        payload = {
            "record_id": record_id,
            "reference_type": reference_type,
            "fragment_id": fragment_id_text,
            "value": value,
        }
        pending = self._proposals.find_pending(self.ADD_TRAVEL_REFERENCE, payload)
        if pending is None:
            pending = self._proposals.add(self.ADD_TRAVEL_REFERENCE, payload)
            self._activity.record(
                ActivityType.ACTION_PROPOSED,
                object_id=str(pending.id),
                details=f"Add travel reference for record {record_id} from fragment {fragment_id}",
            )
        return PresentedReply(
            f"Travel reference proposal {pending.id}: {reference_type} = {value} "
            f"for travel record {record_id}, supported by fragment {fragment_id}.\n\n"
            "The record remains unchanged until approval.",
            (
                ReplyAction("Add reference", f"/approve_action {pending.id}"),
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

    def __init__(
        self,
        tasks: TaskService,
        proposals: ActionProposalRepository,
        activity: ActivityService,
        reminders: TaskReminderService | None = None,
    ) -> None:
        self._tasks = tasks
        self._proposals = proposals
        self._activity = activity
        self._reminders = reminders

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
                + (
                    f" (reminder {reminder.remind_at.isoformat()})"
                    if self._reminders is not None
                    and (reminder := self._reminders.reminder_for_task(task.id or 0)) is not None
                    else ""
                )
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
        return self.propose(argument, chat_id=event.chat_id)

    def propose(self, text: str, *, chat_id: str | None = None) -> str | PresentedReply:
        try:
            title, due_hint, due_at, remind_at = self._tasks.parse_proposal_with_schedule(text)
        except ValueError as error:
            return str(error)
        if remind_at is not None and (self._reminders is None or chat_id is None):
            return "Telegram reminders are only available from an approved Telegram task proposal."
        payload = {
            "title": title,
            "due_hint": due_hint or "",
            "due_at": due_at.isoformat() if due_at else "",
            "remind_at": remind_at.isoformat() if remind_at else "",
            "chat_id": chat_id or "",
        }
        pending = self._proposals.find_pending(self.CREATE_TASK, payload)
        if pending is None:
            pending = self._proposals.add(self.CREATE_TASK, payload)
            self._activity.record(ActivityType.ACTION_PROPOSED, object_id=str(pending.id), details=f"Create task: {title}")
        due_line = f"\nDue cue: {due_hint}" if due_hint else ""
        if due_at:
            due_line += f"\nDue at: {due_at.isoformat()}"
        if remind_at:
            due_line += f"\nReminder at: {remind_at.isoformat()}"
        return PresentedReply(
            f"{due_line.lstrip()}\n\nNo task has been saved yet.",
            (ReplyAction("Accept", f"/approve_action {pending.id}"), ReplyAction("Discard", f"/reject_action {pending.id}")),
            title=f"Save task: {title}", icon="✅",
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

    def handle_command(self, event: IncomingEvent) -> str | PresentedReply | None:
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

    _CACHE_TTL = timedelta(minutes=30)

    def __init__(
        self,
        provider_factory: Callable[[], ResearchProvider | None],
        retention: ResearchRetentionService,
    ) -> None:
        self._provider_factory = provider_factory
        self._retention = retention
        self._ephemeral_bundles: dict[str, tuple[str, datetime, ResearchBundle]] = {}

    def handle_command(self, event: IncomingEvent) -> str | PresentedReply | None:
        command, separator, query = (event.text or "").strip().partition(" ")
        command = command.partition("@")[0]
        if command not in {"/research", "/research_retain", "/research_retain_token", "/research_retain_source_token"}:
            return None
        if command == "/research_retain_source_token":
            token, separator, index_text = query.strip().partition(" ")
            bundle = self._get_ephemeral_bundle(token, event.chat_id)
            if bundle is None:
                return "That research card is no longer available. Run /research again before retaining a source."
            if not separator or not index_text.isdigit():
                return "That source selection is invalid. Run /research again and choose a listed source."
            index = int(index_text)
            if not 1 <= index <= len(bundle.sources):
                return "That source selection is no longer available. Run /research again."
            result = self._retention.retain_source(bundle, bundle.sources[index - 1])
            state = "Already retained" if result.duplicate else "Retained"
            return f"{state} selected external source in Inbox: {result.source.path.name}"
        if command == "/research_retain_token":
            bundle = self._take_ephemeral_bundle(query.strip(), event.chat_id)
            if bundle is None:
                return "That research card is no longer available. Run /research again before retaining it."
            result = self._retention.retain(bundle)
            state = "Already retained" if result.duplicate else "Retained"
            return f"{state} the reviewed external research note in Inbox: {result.source.path.name}"
        if not separator or not query.strip():
            return f"Use {command} followed by a research question."
        provider = self._provider_factory()
        if provider is None:
            return "External research is not configured for this Steward process."
        try:
            bundle = ResearchService(provider).research(query)
        except (ResearchProviderError, ValueError):
            return "External research is temporarily unavailable. Please retry later."
        if command == "/research_retain":
            result = self._retention.retain(bundle)
            state = "Already retained" if result.duplicate else "Retained"
            return f"{state} external research note in Inbox: {result.source.path.name}"
        sources = "\n".join(f"- {source.title}: {source.url}" for source in bundle.sources)
        text = f"External research — ephemeral, not saved:\n\n{bundle.answer}"
        if sources:
            text += f"\n\nExternal sources:\n{sources}"
        token = self._cache_bundle(event.chat_id, bundle)
        actions = [ReplyAction("Keep this reviewed note", f"/research_retain_token {token}")]
        actions.extend(
            ReplyAction(
                f"Keep source {index}: {source.title[:32]}",
                f"/research_retain_source_token {token} {index}",
            )
            for index, source in enumerate(bundle.sources[:8], start=1)
        )
        return PresentedReply(text, tuple(actions))

    def _cache_bundle(self, chat_id: str, bundle: ResearchBundle) -> str:
        self._purge_expired()
        token = secrets.token_urlsafe(12)
        self._ephemeral_bundles[token] = (chat_id, datetime.now(UTC) + self._CACHE_TTL, bundle)
        return token

    def _take_ephemeral_bundle(self, token: str, chat_id: str) -> ResearchBundle | None:
        self._purge_expired()
        item = self._ephemeral_bundles.pop(token, None)
        if item is None or item[0] != chat_id:
            return None
        return item[2]

    def _get_ephemeral_bundle(self, token: str, chat_id: str) -> ResearchBundle | None:
        """Read a chat-bound card without consuming it, for multiple selections."""
        self._purge_expired()
        item = self._ephemeral_bundles.get(token)
        if item is None or item[0] != chat_id:
            return None
        return item[2]

    def _purge_expired(self) -> None:
        now = datetime.now(UTC)
        for token in [key for key, (_, expires_at, _) in self._ephemeral_bundles.items() if expires_at <= now]:
            del self._ephemeral_bundles[token]


class StewardCuratedNoteApplication:
    """Stage explicitly selected content before it becomes a canonical source."""

    CREATE_CURATED_NOTE = "create_curated_note"

    def __init__(
        self,
        proposals: ActionProposalRepository,
        activity: ActivityService,
        *,
        local_model: ModelGateway | None = None,
        external_model: ModelGateway | None = None,
    ) -> None:
        self._proposals = proposals
        self._activity = activity
        self._local_model = local_model
        self._external_model = external_model

    def handle_command(self, event: IncomingEvent) -> str | PresentedReply | None:
        command, separator, text = (event.text or "").strip().partition(" ")
        command = command.partition("@")[0]
        if command not in {"/propose_note", "/curate", "/curate_synthesize"}:
            return None
        if command == "/curate_synthesize":
            return self._synthesize_reply(separator, text, event)
        if command == "/curate":
            note = (event.reply_text or "").strip()
            origin = "user-selected Telegram reply"
            if not note:
                return "Reply to a text discussion message with /curate to stage it as a curated note."
        else:
            note = text.strip()
            origin = "user-supplied note"
        if command == "/propose_note" and (not separator or not note):
            return "Use /propose_note followed by the curated note you want to retain."
        payload = {"text": note, "origin": origin}
        pending = self._proposals.find_pending(self.CREATE_CURATED_NOTE, payload)
        if pending is None:
            pending = self._proposals.add(self.CREATE_CURATED_NOTE, payload)
            self._activity.record(ActivityType.ACTION_PROPOSED, object_id=str(pending.id), details=f"Create curated Inbox note from {origin}")
        preview = note if len(note) <= 500 else note[:497] + "..."
        return PresentedReply(
            f"Curated note proposal {pending.id} ({origin}):\n{preview}\n\nIt has not been saved.",
            (ReplyAction("Save note", f"/approve_action {pending.id}"), ReplyAction("Discard", f"/reject_action {pending.id}")),
        )

    def _synthesize_reply(
        self, separator: str, argument: str, event: IncomingEvent
    ) -> str | PresentedReply:
        """Make a non-persisted, explicitly model-bound note candidate."""
        source_text = (event.reply_text or "").strip()
        if not source_text:
            return "Reply to a text discussion message with /curate_synthesize to create a note draft."
        mode = argument.strip().casefold() if separator else "local"
        if mode not in {"local", "external"}:
            return "Use /curate_synthesize by replying to a message, optionally followed by local or external."
        model = self._local_model if mode == "local" else self._external_model
        if model is None:
            boundary = "local model" if mode == "local" else "external model"
            return f"No {boundary} is configured for curated-note synthesis."
        try:
            note = model.generate(
                instructions=(
                    "Create a concise Markdown study note from only the supplied user discussion. "
                    "Do not introduce new facts, citations, or external research. Preserve uncertainty. "
                    "Use a short title and bullets where useful."
                ),
                input_text=source_text,
            ).strip()
        except ModelGatewayError:
            return f"The configured {mode} model is temporarily unavailable; no note was saved."
        if not note:
            return f"The configured {mode} model returned no note; nothing was saved."
        note = note[:6000]
        origin = f"model-synthesized user-selected Telegram reply ({mode} model)"
        payload = {"text": note, "origin": origin}
        pending = self._proposals.find_pending(self.CREATE_CURATED_NOTE, payload)
        if pending is None:
            pending = self._proposals.add(self.CREATE_CURATED_NOTE, payload)
            self._activity.record(
                ActivityType.ACTION_PROPOSED,
                object_id=str(pending.id),
                details=f"Create curated Inbox note from {mode} model synthesis",
            )
        preview = note if len(note) <= 500 else note[:497] + "..."
        return PresentedReply(
            f"Curated note proposal {pending.id} ({origin}):\n{preview}\n\nIt has not been saved.",
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
        connector: KnowledgeConnector | None = None,
        source_repository: SourceRepository | None = None,
    ) -> None:
        self._knowledge = knowledge
        self._fragments = fragments
        self._proposals = proposals
        self._activity = activity
        self._connector = connector
        self._sources = source_repository

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
        if command == "/connect_knowledge":
            if self._connector is None:
                return "Knowledge connection inspection is not configured for this Steward process."
            proposals = self._connector.propose()
            if not proposals:
                return "No evidence-backed knowledge connections found."
            return "Evidence-backed knowledge connections:\n" + "\n\n".join(
                f"{proposal.left_concept_name} ↔ {proposal.right_concept_name}\n"
                f"Evidence fragments: {', '.join(str(item) for item in proposal.supporting_fragment_ids)}\n"
                f"{proposal.rationale}\n"
                f"Limit: {proposal.where_analogy_breaks}"
                for proposal in proposals[:10]
            )
        if command == "/knowledge_proposals":
            pending = [proposal for proposal in self._proposals.list_all() if proposal.status == "pending"]
            if not pending:
                return "No pending knowledge enrichment proposals."
            return "Pending knowledge proposals:\n" + "\n".join(
                f"{proposal.id}: {proposal.operation.value} claim {proposal.claim_id} "
                f"with fragment {proposal.fragment_id}" for proposal in pending
            )
        if command == "/knowledge_proposal":
            if not separator or not argument.strip().isdigit():
                return "Use /knowledge_proposal followed by a numeric proposal ID."
            proposal = self._proposals.get(int(argument.strip()))
            if proposal is None:
                return f"Knowledge proposal {argument.strip()} was not found."
            if proposal.status == "pending":
                return PresentedReply(
                    self._render_enrichment(proposal),
                    (
                        ReplyAction("Accept", f"/review_enrichment {proposal.id} accepted"),
                        ReplyAction("Reject", f"/review_enrichment {proposal.id} rejected"),
                    ),
                )
            return self._render_enrichment(proposal)
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
                self._render_enrichment(stored),
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
            if proposal.operation is EnrichmentOperation.CONTRADICT and proposal.status == "accepted":
                return f"Knowledge contradiction proposal {proposal.id} accepted. Existing claim unchanged."
            return f"Knowledge enrichment proposal {proposal.id} {proposal.status}."
        return None

    def _render_enrichment(self, proposal: StoredKnowledgeEnrichmentProposal) -> str:
        """Render both sides of an evidence review without creating a synthesis."""
        claim = self._knowledge.get_claim(proposal.claim_id)
        fragment = self._fragments.get(proposal.fragment_id)
        claim_text = claim.text if claim is not None else "(claim is no longer available)"
        if fragment is None:
            evidence = "(supporting fragment is no longer available)"
            provenance = ""
        else:
            source = self._sources.get_by_id(fragment.source_id) if self._sources is not None else None
            provenance = f" from {source.path.name}" if source is not None else ""
            evidence = fragment.text[:600]
        operation = proposal.operation
        warning = (
            "\n\nPotential contradiction: accepting records your review only; it does not rewrite the existing claim."
            if operation is EnrichmentOperation.CONTRADICT else ""
        )
        return (
            f"Knowledge proposal {proposal.id}: {operation.value.upper()} claim {proposal.claim_id}\n"
            f"Existing claim {proposal.claim_id}: {claim_text}\n"
            f"Evidence fragment {proposal.fragment_id}{provenance}: {evidence}\n"
            f"Rationale: {proposal.rationale}{warning}"
        )


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

    def __init__(
        self,
        privacy: PrivacyService,
        sources: SourceRepository,
        activity: ActivityService | None = None,
    ) -> None:
        self._privacy = privacy
        self._sources = sources
        self._activity = activity

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
        previous_rule = self._privacy.rule_for(source_id)
        self._privacy.set_rule(source_id, rule)
        if self._activity is not None and previous_rule != rule:
            self._activity.record(
                ActivityType.SOURCE_PRIVACY_CHANGED,
                object_id=str(source_id),
                details=f"{previous_rule.value} -> {rule.value}",
            )
        return f"Source {source_id} privacy rule set to {rule.value}."


class StewardCalendarApplication:
    """Read current Calendar state through a lazy, locally authorized adapter."""

    def __init__(self, calendar_factory: Callable[[], CalendarService] | None) -> None:
        self._calendar_factory = calendar_factory

    def handle_command(self, event: IncomingEvent) -> str | None:
        command, separator, argument = (event.text or "").strip().partition(" ")
        command = command.partition("@")[0]
        if command not in {"/calendar", "/calendar_search", "/calendar_get"}:
            return None
        if self._calendar_factory is None:
            return "Calendar is not configured locally. Complete Calendar authorization on the local machine first."
        if command == "/calendar_get" and (not separator or not argument.strip()):
            return "Use /calendar_get followed by a Calendar event ID."
        try:
            calendar = self._calendar_factory()
            if command == "/calendar_get":
                event_result = calendar.get_event(argument.strip())
                return PresentedReply(
                    f"{event_result.start} → {event_result.end}\n\n"
                    f"Calendar ID: {event_result.id}",
                    title=event_result.summary,
                    icon="📅",
                )
            events = calendar.search(argument.strip(), limit=10)
        except Exception:
            return "Calendar is temporarily unavailable. Verify local authorization, then try again."
        if not events:
            return "No current Calendar events matched."
        lines = []
        actions: list[ReplyAction] = []
        for index, item in enumerate(events, start=1):
            lines.append(f"{index}. {item.summary}\n{item.start} → {item.end}")
            actions.append(ReplyAction(f"Open {index}", f"/calendar_get {item.id}"))
        return PresentedReply(
            "\n\n".join(lines), tuple(actions), title="Calendar events", icon="📅"
        )

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

    def handle(self, event: IncomingEvent) -> str | PresentedReply:
        """Return a response without knowing which external platform sent it."""

        if not event.text or not event.text.strip():
            return TEXT_QUESTION_REQUIRED

        result = self._graph.invoke(
            {"question": event.text},
            {"configurable": {"thread_id": f"{event.platform}:{event.chat_id}"}},
        )
        return self._format_response(result)

    @staticmethod
    def _format_response(result: QuestionResult) -> str | PresentedReply:
        """Render grounded evidence as a concise transport-neutral answer card."""

        citations = result.get("citations", ())
        if not citations:
            return result["answer"]

        source_lines = []
        for citation in citations:
            heading = citation.heading or "Preamble"
            source_lines.append(
                f"[{citation.key}] {citation.source_path.name}:"
                f"{citation.location} [{heading}]"
            )
        return PresentedReply(
            f"{result['answer']}\n\nSources:\n" + "\n".join(source_lines),
            title="Answer from your saved material",
            icon="🧠",
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
    ) -> None:
        self._service = service
        self._contexts = contexts

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
        explicit_prefixes = ("note:", "thought:", "remember:", "deadline:", "todo:", "task:")
        personal_signals = (
            "flight", "itinerary", "booking", "reservation", "receipt", "invoice", "warranty",
            "deadline", "due ", "submit ", "remind me", "to do", "todo", "task",
        )
        return (
            len(text) >= 280
            or text.count("\n") >= 2
            or normalized.startswith(explicit_prefixes)
            or (len(text) >= 24 and any(signal in normalized for signal in personal_signals))
        )

    def handle_command(self, event: IncomingEvent) -> CaptureResult | str | None:
        command, separator, argument = (event.text or "").strip().partition(" ")
        command = command.partition("@")[0]
        if command not in {"/intake_accept", "/intake_discard", "/intake_context", "/intake_analysis"}:
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
            intake = self._service.discard(intake_id, event.chat_id)
        except OSError:
            return "Could not update this provisional intake because local staging is temporarily unavailable. Try again later."
        except ValueError as error:
            return str(error)
        return f"Discarded provisional intake {intake.id}; its staged copy was removed."

    def handle_followup(self, event: IncomingEvent) -> PresentedReply | str | None:
        """Attach the next ordinary message to an explicitly requested intake context."""

        if self._contexts is None or not (event.text or "").strip() or (event.text or "").startswith("/"):
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
        return PresentedReply(
            f"Type: {intake.category}\nSummary: {intake.summary}\n\n"
            f"{description}\n\nIt is staged locally and has not been saved.",
            self._actions(intake),
            title=f"Review {intake.original_name}",
            icon="📄",
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
            ReplyAction("Do not keep", f"/intake_discard {intake.id}"),
        )


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
        except (OSError, ValueError):
            return "Drive import is temporarily unavailable. Verify local authorization, then try again."
        status = "Already imported" if result.duplicate else "Imported Drive file to Inbox"
        return f"{status}: {result.source.path.name}"

    def _search(self, query: str) -> str | PresentedReply:
        if self._importer is None or not hasattr(self._importer, "search"):
            return "Drive search is not configured on this Steward process. Authorize Drive locally first."
        try:
            results = self._importer.search(query.strip())
        except (OSError, ValueError):
            return "Drive search is temporarily unavailable. Verify local authorization, then try again."
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
        except (OSError, ValueError):
            return "Gmail import is temporarily unavailable. Verify local authorization, then try again."
        status = "Already imported" if result.duplicate else "Imported Gmail message to Inbox"
        return f"{status}: {result.source.path.name}"

    def _search(self, query: str) -> str | PresentedReply:
        if self._importer is None or not hasattr(self._importer, "search"):
            return "Gmail search is not configured on this Steward process. Authorize Gmail locally first."
        try:
            results = self._importer.search(query.strip())
        except (OSError, ValueError):
            return "Gmail search is temporarily unavailable. Verify local authorization, then try again."
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
        return self._start_proposal(event, proposal)

    def _start_proposal(self, event: IncomingEvent, proposal: OrganizationProposal) -> PresentedReply:
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
        return self._render_proposal(proposal_id, proposal)

    def _render_proposal(self, proposal_id: int, proposal: OrganizationProposal) -> PresentedReply:
        """Explain the exact source, outcome, rationale, and safe choices in one card."""

        source = self._sources.get_by_id(proposal.source_id) if self._sources is not None else None
        filename = (
            source.path.name if source is not None
            else proposal.suggested_path.name if proposal.suggested_path is not None
            else "saved source"
        )
        if proposal.suggested_path is None:
            return PresentedReply(
                f"Suggested destination: Inbox\nWhy: {proposal.rationale}\n"
                "Effect: the original stays in Inbox.\n\n"
                "Reply with a workspace name if you want to guide this suggestion.",
                (
                    ReplyAction("Keep in Inbox", f"/organization_accept {proposal_id}"),
                    ReplyAction("Reject", f"/organization_reject {proposal_id}"),
                ),
                title=f"Organize {filename}", icon="📁",
            )
        workspace = next(
            (item for item in self._workspaces.list_all() if item.id == proposal.workspace_id), None
        )
        destination = (
            f"workspace {workspace.name} ({proposal.suggested_path.name})"
            if workspace is not None else f"the proposed workspace ({proposal.suggested_path.name})"
        )
        return PresentedReply(
            f"Suggested destination: {destination}\nWhy: {proposal.rationale}\n"
            "Effect: accepting moves the original file.\n\n"
            "Reply with a workspace name to change this suggestion.",
            (
                ReplyAction("Accept", f"/organization_accept {proposal_id}"),
                ReplyAction("Keep in Inbox", f"/organization_keep_inbox {proposal_id}"),
                ReplyAction("Reject", f"/organization_reject {proposal_id}"),
            ),
            title=f"Organize {filename}", icon="📁",
        )

    def handle_decision(self, event: IncomingEvent) -> str | None:
        """Resume exactly this chat's paused graph for an explicit decision."""

        if (event.text or "").strip().partition(" ")[0].partition("@")[0] == "/organization_proposals":
            return self.list_proposals()
        pending = self._threads.get_pending(event.platform, event.chat_id)
        if pending is None:
            return None
        response = (event.text or "").strip()
        normalized_response = response.casefold().rstrip("?!.").strip()
        command, _, argument = response.partition(" ")
        command = command.casefold()
        if command == "/organization_context":
            proposal_identifier, separator, guidance = argument.partition(" ")
            if not proposal_identifier.isdigit() or int(proposal_identifier) != pending.proposal_id or not separator or not guidance.strip():
                return (
                    f"Use /organization_context {pending.proposal_id} followed by an existing workspace name."
                )
            return self._revise_with_context(event, pending, guidance)
        if command == "/organization_new_workspace":
            proposal_identifier, separator, workspace_name = argument.partition(" ")
            if not proposal_identifier.isdigit() or int(proposal_identifier) != pending.proposal_id or not separator or not workspace_name.strip():
                return f"Use /organization_new_workspace {pending.proposal_id} followed by a new workspace name."
            return self._revise_with_new_workspace(event, pending, workspace_name)
        if command == "/organization_keep_inbox":
            if not argument.isdigit() or int(argument) != pending.proposal_id:
                return f"Use /organization_keep_inbox followed by proposal {pending.proposal_id}."
            return self._revise_keep_in_inbox(event, pending)
        if command == "/organization_accept" and argument.isdigit() and int(argument) == pending.proposal_id:
            decision = "accepted"
        elif command == "/organization_reject" and argument.isdigit() and int(argument) == pending.proposal_id:
            decision = "rejected"
        elif normalized_response in {"accept", "accepted"}:
            decision = "accepted"
        elif normalized_response in {"reject", "rejected"}:
            decision = "rejected"
        elif normalized_response in {"what is this", "what is this proposal", "why", "details", "show details"}:
            proposal = self._proposals.get(pending.proposal_id)
            if proposal is None:
                return "That organization proposal is no longer available. Send /pending for the current list."
            return self._render_proposal(pending.proposal_id, proposal)
        elif normalized_response in {"yes", "y", "okay", "ok"}:
            decision = "accepted"
        elif normalized_response in {"no", "n"}:
            decision = "rejected"
        elif response.casefold().startswith(("put it in ", "move it to ", "put this in ")):
            guidance = response.partition(" ")[2]
            if response.casefold().startswith("put it in "):
                guidance = response[len("put it in "):]
            elif response.casefold().startswith("move it to "):
                guidance = response[len("move it to "):]
            else:
                guidance = response[len("put this in "):]
            return self._revise_with_context(event, pending, guidance)
        elif any(workspace.name.casefold() == normalized_response for workspace in self._workspaces.list_all()):
            return self._revise_with_context(event, pending, response)
        else:
            return (
                "I am waiting for your decision. Reply `yes`, `no`, `what is this?`, "
                "or name the workspace you want."
            )
        from langgraph.types import Command

        completed = self._graph.invoke(
            Command(resume=decision),
            {"configurable": {"thread_id": pending.thread_id}},
        )
        if completed.get("status") != decision:
            raise RuntimeError("Organization approval did not reach a final status.")
        self._threads.finish(event.platform, event.chat_id, decision)
        proposal = self._proposals.get(pending.proposal_id)
        if proposal is None:
            return "Your organization decision was saved."
        source = self._sources.get_by_id(proposal.source_id) if self._sources is not None else None
        filename = source.path.name if source is not None else "the source"
        if decision == "rejected":
            return f"Did not organize {filename}."
        if proposal.suggested_path is None:
            return f"Kept {filename} in Inbox."
        workspace = next(
            (item for item in self._workspaces.list_all() if item.id == proposal.workspace_id), None
        )
        target = proposal.workspace_name or (
            workspace.name if workspace is not None else "the selected destination"
        )
        return f"Moved {filename} to {target}."

    def list_proposals(self) -> str:
        """Show bounded, path-free organization history for Telegram review."""
        proposals = self._proposals.list_all()[-10:]
        if not proposals:
            return "No organization proposals yet."
        lines = ["Recent organization proposals:"]
        for proposal in reversed(proposals):
            source_name = None
            if self._sources is not None:
                source = self._sources.get_by_id(proposal.source_id)
                source_name = source.path.name if source is not None else None
            target = proposal.workspace_name or (
                f"workspace {proposal.workspace_id}" if proposal.workspace_id is not None else "Inbox"
            )
            lines.append(
                f"{proposal.id}: source {proposal.source_id}"
                + (f" ({source_name})" if source_name else "")
                + f" -> {target} [{proposal.status}]"
            )
        return "\n".join(lines)

    def _revise_with_context(
        self, event: IncomingEvent, pending: PendingOrganizationApproval, guidance: str
    ) -> str | PresentedReply:
        if self._sources is None:
            return "Organization context revision is not configured for this Steward process."
        previous = self._proposals.get(pending.proposal_id)
        source = self._sources.get_by_id(previous.source_id) if previous is not None else None
        if source is None:
            return "The source for this organization proposal was not found."
        proposal = OrganizationService().propose_with_context(source, self._workspaces.list_all(), guidance)
        self._proposals.set_status(pending.proposal_id, "rejected")
        self._threads.finish(event.platform, event.chat_id, "rejected")
        self._activity.record(
            ActivityType.ORGANIZATION_REJECTED,
            object_id=str(pending.proposal_id),
            details="Superseded after user supplied organization context.",
        )
        if proposal.suggested_path is None:
            return (
                "Your context did not name exactly one existing workspace, so the source remains in Inbox. "
                "Create or choose a workspace explicitly, then organize again."
            )
        return self._start_proposal(event, proposal)

    def _revise_with_new_workspace(
        self, event: IncomingEvent, pending: PendingOrganizationApproval, workspace_name: str
    ) -> str | PresentedReply:
        if self._sources is None:
            return "New-workspace organization proposals are not configured for this Steward process."
        normalized_name = " ".join(workspace_name.split())
        if not normalized_name:
            return "A new workspace name must not be empty."
        if any(workspace.name.casefold() == normalized_name.casefold() for workspace in self._workspaces.list_all()):
            return f"Workspace {normalized_name!r} already exists. Use /organization_context instead."
        previous = self._proposals.get(pending.proposal_id)
        source = self._sources.get_by_id(previous.source_id) if previous is not None else None
        if source is None:
            return "The source for this organization proposal was not found."
        vault_root = source.path.parent.parent if source.path.parent.name.casefold() == "inbox" else source.path.parent
        proposal = OrganizationProposal(
            None,
            source.id or 0,
            "create_workspace_and_move",
            None,
            vault_root / "projects" / normalized_name / source.path.name,
            f"You requested a new workspace named '{normalized_name}'.",
            1.0,
            workspace_name=normalized_name,
        )
        self._proposals.set_status(pending.proposal_id, "rejected")
        self._threads.finish(event.platform, event.chat_id, "rejected")
        self._activity.record(
            ActivityType.ORGANIZATION_REJECTED,
            object_id=str(pending.proposal_id),
            details="Superseded after user requested a new workspace.",
        )
        return self._start_proposal(event, proposal)

    def _revise_keep_in_inbox(
        self, event: IncomingEvent, pending: PendingOrganizationApproval
    ) -> str | PresentedReply:
        """Replace a proposed move with an explicitly accepted Inbox outcome."""
        if self._sources is None:
            return "Inbox organization revision is not configured for this Steward process."
        previous = self._proposals.get(pending.proposal_id)
        source = self._sources.get_by_id(previous.source_id) if previous is not None else None
        if source is None:
            return "The source for this organization proposal was not found."
        proposal = OrganizationProposal(
            None,
            source.id or 0,
            "keep_in_inbox",
            None,
            None,
            "You chose to keep this original in Inbox.",
            1.0,
        )
        self._proposals.set_status(pending.proposal_id, "rejected")
        self._threads.finish(event.platform, event.chat_id, "rejected")
        self._activity.record(
            ActivityType.ORGANIZATION_REJECTED,
            object_id=str(pending.proposal_id),
            details="Superseded after user chose to keep the source in Inbox.",
        )
        return self._start_proposal(event, proposal)

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
            if response is not None:
                return response
        return f"I inspected {len(inbox_sources)} Inbox source(s), but no organization proposal was created."

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

    REEXTRACT_SOURCE = "reextract_source"
    REBUILD_SEMANTIC_INDEX = "rebuild_semantic_index"
    UNREGISTER_SOURCE = "unregister_source"

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
        task_reminder_service: TaskReminderService | None = None,
        capture_service: InboxCaptureService | None = None,
        workspace_repository: WorkspaceRepository | None = None,
        source_repository: SourceRepository | None = None,
        delivery_repository: TelegramUpdateDeliveryRepository | None = None,
        source_service: SourceService | None = None,
        semantic_index_rebuilder: Callable[[], int] | None = None,
    ) -> None:
        self._repository = repository
        self._service = service
        self._calendar_proposals = calendar_proposals
        self._calendar_writer_factory = calendar_writer_factory
        self._records = record_service
        self._fragments = fragment_repository
        self._activity = activity_service
        self._tasks = task_service
        self._task_reminders = task_reminder_service
        self._capture = capture_service
        self._workspaces = workspace_repository
        self._sources = source_repository
        self._delivery_repository = delivery_repository
        self._source_service = source_service
        self._semantic_index_rebuilder = semantic_index_rebuilder

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
        if command == "/propose_reextract":
            return self.propose_reextract(argument)
        if command == "/propose_rebuild_index":
            return self.propose_rebuild_semantic_index(argument)
        if command == "/propose_unregister_source":
            return self.propose_unregister_source(argument)
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
        if proposal is not None and proposal.action_type == self.REEXTRACT_SOURCE:
            return self._review_reextract(proposal_id, decision)
        if proposal is not None and proposal.action_type == self.REBUILD_SEMANTIC_INDEX:
            return self._review_rebuild_semantic_index(proposal_id, decision)
        if proposal is not None and proposal.action_type == self.UNREGISTER_SOURCE:
            return self._review_unregister_source(proposal_id, decision)
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
        if proposal is not None and proposal.action_type == StewardRecordApplication.ADD_TRAVEL_REFERENCE:
            return self._review_travel_reference(proposal_id, decision)
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
        title = proposal.payload.get("title", "task")
        if proposal.status == decision:
            return f"Task “{title}” was already {proposal.status}."
        if proposal.status != "pending":
            return f"Task “{title}” was already {proposal.status}."
        if decision == "rejected":
            self._repository.set_status(proposal_id, decision)
            if self._activity is not None:
                self._activity.record(ActivityType.ACTION_REJECTED, object_id=str(proposal_id), details=proposal.action_type)
            return f"Discarded task: {title}."
        due_at_value = proposal.payload.get("due_at") or None
        remind_at_value = proposal.payload.get("remind_at") or None
        if remind_at_value and (self._task_reminders is None or not proposal.payload.get("chat_id")):
            return "Task reminder delivery is not configured for this proposal."
        task = self._tasks.create(
            proposal.payload["title"],
            proposal.payload.get("due_hint") or None,
            TaskService.parse_due_at(due_at_value) if due_at_value else None,
        )
        if remind_at_value:
            self._task_reminders.schedule(
                task.id or 0,
                proposal.payload["chat_id"],
                TaskService.parse_due_at(remind_at_value),
            )
        self._repository.set_status(proposal_id, decision)
        if self._activity is not None:
            self._activity.record(ActivityType.TASK_CREATED, object_id=str(task.id), details=task.title)
            self._activity.record(ActivityType.ACTION_ACCEPTED, object_id=str(proposal_id), details=proposal.action_type)
        return f"Saved task: {task.title}."

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

    def _review_reextract(self, proposal_id: int, decision: str) -> str:
        """Refresh derived text after a review without changing canonical input.

        This deliberately has a narrow payload: a registered source ID.  The
        Telegram caller cannot supply a path, parser option, or arbitrary
        filesystem target.
        """
        if self._source_service is None:
            return "Source re-extraction is not configured for this Steward process."
        proposal = self._repository.get(proposal_id)
        if proposal is None:
            return "Re-extraction proposal was not found."
        if proposal.status == decision:
            return f"Re-extraction proposal {proposal.id} {proposal.status}."
        if proposal.status != "pending":
            return f"Re-extraction proposal {proposal.id} was already {proposal.status}."
        if decision == "rejected":
            self._repository.set_status(proposal_id, decision)
            if self._activity is not None:
                self._activity.record(
                    ActivityType.ACTION_REJECTED,
                    object_id=str(proposal_id),
                    details=proposal.action_type,
                )
            return f"Re-extraction proposal {proposal.id} rejected."
        try:
            fragments = self._source_service.reextract_source(int(proposal.payload["source_id"]))
        except ValueError as error:
            # Preserve the pending proposal for a deliberate retry after the
            # local file or extractor problem is repaired.
            return f"Could not refresh derived text: {error}"
        except (OSError, UnicodeDecodeError):
            return "Could not refresh derived text; the local source or extractor is unavailable. The proposal remains pending."
        self._repository.set_status(proposal_id, decision)
        if self._activity is not None:
            self._activity.record(
                ActivityType.SOURCE_REEXTRACTED,
                object_id=proposal.payload["source_id"],
                details=f"fragments:{len(fragments)}",
            )
            self._activity.record(
                ActivityType.ACTION_ACCEPTED,
                object_id=str(proposal_id),
                details=proposal.action_type,
            )
        return (
            f"Refreshed derived text for source {proposal.payload['source_id']}: "
            f"{len(fragments)} fragments. Original unchanged."
        )

    def _review_rebuild_semantic_index(self, proposal_id: int, decision: str) -> str:
        """Rebuild vectors only after a deliberate, owner-visible decision."""
        if self._semantic_index_rebuilder is None:
            return "Semantic-index rebuilding is not configured for this Steward process."
        proposal = self._repository.get(proposal_id)
        if proposal is None:
            return "Semantic-index rebuild proposal was not found."
        if proposal.status == decision:
            return f"Semantic-index rebuild proposal {proposal.id} {proposal.status}."
        if proposal.status != "pending":
            return f"Semantic-index rebuild proposal {proposal.id} was already {proposal.status}."
        if decision == "rejected":
            self._repository.set_status(proposal_id, decision)
            if self._activity is not None:
                self._activity.record(ActivityType.ACTION_REJECTED, object_id=str(proposal_id), details=proposal.action_type)
            return f"Semantic-index rebuild proposal {proposal.id} rejected."
        try:
            indexed = self._semantic_index_rebuilder()
        except (ImportError, OSError, RuntimeError, ValueError):
            # Model/cache failures must not turn into filesystem or provider
            # diagnostics in Telegram, and keep the review available to retry.
            return "Could not rebuild the local semantic index. Verify the local embedding model, then retry the pending proposal."
        self._repository.set_status(proposal_id, decision)
        if self._activity is not None:
            self._activity.record(ActivityType.SEMANTIC_INDEX_REBUILT, object_id=str(proposal_id), details=f"fragments:{indexed}")
            self._activity.record(ActivityType.ACTION_ACCEPTED, object_id=str(proposal_id), details=proposal.action_type)
        return f"Rebuilt local semantic index for {indexed} fragments. Original files unchanged."

    def _review_unregister_source(self, proposal_id: int, decision: str) -> str:
        """Remove only registry metadata after explicit review."""
        if self._sources is None:
            return "Source unregistering is not configured for this Steward process."
        proposal = self._repository.get(proposal_id)
        if proposal is None:
            return "Source-unregister proposal was not found."
        if proposal.status == decision:
            return f"Source-unregister proposal {proposal.id} {proposal.status}."
        if proposal.status != "pending":
            return f"Source-unregister proposal {proposal.id} was already {proposal.status}."
        if decision == "rejected":
            self._repository.set_status(proposal_id, decision)
            if self._activity is not None:
                self._activity.record(ActivityType.ACTION_REJECTED, object_id=str(proposal_id), details=proposal.action_type)
            return f"Source-unregister proposal {proposal.id} rejected."
        try:
            removed = self._sources.unregister(int(proposal.payload["source_id"]))
        except ValueError:
            return "That source is no longer registered. The proposal remains pending for review."
        self._repository.set_status(proposal_id, decision)
        if self._activity is not None:
            self._activity.record(
                ActivityType.SOURCE_UNREGISTERED,
                object_id=str(removed.id),
                details="Unregistered local metadata; original file retained.",
            )
            self._activity.record(ActivityType.ACTION_ACCEPTED, object_id=str(proposal_id), details=proposal.action_type)
        return f"Unregistered source {removed.id} from Steward metadata. Original file unchanged."

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
        origin = proposal.payload.get("origin", "user-supplied note")
        text = f"# Curated note\n\nOrigin: {origin}\n\n{proposal.payload['text']}\n"
        result = self._capture.capture_text(
            IncomingEvent(
                id=f"curated-note:{proposal_id}", platform="curated_note", chat_id=event.chat_id,
                message_id=str(proposal_id), reply_to_id=event.reply_to_id, timestamp=event.timestamp,
                text=text, attachments=(),
            )
        )
        self._repository.set_status(proposal_id, decision)
        if self._activity is not None:
            self._activity.record(ActivityType.ACTION_ACCEPTED, object_id=str(proposal_id), details=proposal.action_type)
        state = "Already saved" if result.duplicate else "Saved"
        return f"{state} curated note to Inbox: {result.source.path.name}"

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

    def _review_travel_reference(self, proposal_id: int, decision: str) -> str:
        if self._records is None:
            return "Travel-reference review is not configured on this Steward process."
        proposal = self._repository.get(proposal_id)
        if proposal is None:
            return "Travel-reference proposal was not found."
        if proposal.status == decision:
            return f"Travel-reference proposal {proposal.id} {proposal.status}."
        if proposal.status != "pending":
            return f"Travel-reference proposal {proposal.id} was already {proposal.status}."
        if decision == "rejected":
            self._repository.set_status(proposal_id, decision)
            if self._activity is not None:
                self._activity.record(ActivityType.ACTION_REJECTED, object_id=str(proposal_id), details=proposal.action_type)
            return f"Travel-reference proposal {proposal.id} rejected."
        try:
            reference = self._records.add_reference(
                int(proposal.payload["record_id"]),
                proposal.payload["reference_type"],
                proposal.payload["value"],
                int(proposal.payload["fragment_id"]),
            )
        except ValueError as error:
            return str(error)
        self._repository.set_status(proposal_id, decision)
        if self._activity is not None:
            self._activity.record(
                ActivityType.TRAVEL_REFERENCE_ADDED,
                object_id=str(reference.id),
                details=f"record:{reference.travel_record_id} fragment:{reference.fragment_id}",
            )
            self._activity.record(ActivityType.ACTION_ACCEPTED, object_id=str(proposal_id), details=proposal.action_type)
        return (
            f"Travel reference {reference.id} added to record {reference.travel_record_id}: "
            f"{reference.reference_type} (fragment {reference.fragment_id})."
        )

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

    def propose_reextract(self, source_id_text: str) -> str | PresentedReply:
        """Stage a narrow, review-required derived-data refresh."""
        if self._source_service is None or self._sources is None:
            return "Source re-extraction is not configured for this Steward process."
        if not source_id_text.strip().isdigit():
            return "Use /propose_reextract followed by a numeric source ID."
        source_id = int(source_id_text.strip())
        source = self._sources.get_by_id(source_id)
        if source is None:
            return f"Source {source_id} was not found."
        payload = {"source_id": str(source_id)}
        proposal = self._repository.find_pending(self.REEXTRACT_SOURCE, payload)
        if proposal is None:
            proposal = self._repository.add(self.REEXTRACT_SOURCE, payload)
            if self._activity is not None:
                self._activity.record(
                    ActivityType.ACTION_PROPOSED,
                    object_id=str(proposal.id),
                    details=f"Re-extract derived text for source:{source_id}",
                )
        return PresentedReply(
            f"Re-extraction proposal {proposal.id} is pending for source {source_id}. "
            "This refreshes derived text only; the original file will not change.",
            (
                ReplyAction("Refresh derived text", f"/approve_action {proposal.id}"),
                ReplyAction("Reject", f"/reject_action {proposal.id}"),
            ),
        )

    def propose_rebuild_semantic_index(self, argument: str) -> str | PresentedReply:
        """Stage a model-costly but rebuildable local index operation."""
        if self._semantic_index_rebuilder is None:
            return "Semantic-index rebuilding is not configured for this Steward process."
        if argument.strip():
            return "Use /propose_rebuild_index without arguments."
        proposal = self._repository.find_pending(self.REBUILD_SEMANTIC_INDEX, {})
        if proposal is None:
            proposal = self._repository.add(self.REBUILD_SEMANTIC_INDEX, {})
            if self._activity is not None:
                self._activity.record(ActivityType.ACTION_PROPOSED, object_id=str(proposal.id), details="Rebuild local semantic index")
        return PresentedReply(
            f"Semantic-index rebuild proposal {proposal.id} is pending. It will regenerate local vectors from existing fragments and may take time; original files will not change.",
            (
                ReplyAction("Rebuild local index", f"/approve_action {proposal.id}"),
                ReplyAction("Reject", f"/reject_action {proposal.id}"),
            ),
        )

    def propose_unregister_source(self, source_id_text: str) -> str | PresentedReply:
        """Stage metadata removal without accepting a filesystem target from chat."""
        if self._sources is None:
            return "Source unregistering is not configured for this Steward process."
        if not source_id_text.strip().isdigit():
            return "Use /propose_unregister_source followed by a numeric source ID."
        source = self._sources.get_by_id(int(source_id_text.strip()))
        if source is None:
            return f"Source {source_id_text.strip()} was not found."
        payload = {"source_id": str(source.id)}
        proposal = self._repository.find_pending(self.UNREGISTER_SOURCE, payload)
        if proposal is None:
            proposal = self._repository.add(self.UNREGISTER_SOURCE, payload)
            if self._activity is not None:
                self._activity.record(
                    ActivityType.ACTION_PROPOSED,
                    object_id=str(proposal.id),
                    details=f"Unregister source metadata: {source.id}",
                )
        return PresentedReply(
            f"Unregister proposal {proposal.id}: remove Steward metadata for source {source.id} ({source.path.name}). "
            "Its original file will not be deleted.",
            (
                ReplyAction("Unregister metadata", f"/approve_action {proposal.id}"),
                ReplyAction("Keep registered", f"/reject_action {proposal.id}"),
            ),
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
        review_inbox_application: StewardReviewInboxApplication | None = None,
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
        self._review_inbox_application = review_inbox_application
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
        if self._review_inbox_application is not None:
            review_response = self._review_inbox_application.handle_command(event)
            if review_response is not None:
                return review_response
            review_followup = self._review_inbox_application.handle_followup(event)
            if review_followup is not None:
                return review_followup
            natural_review = self._review_inbox_application.handle_natural_request(event)
            if natural_review is not None:
                return natural_review
        if self._task_application is not None:
            normalized = (event.text or "").strip().casefold()
            if normalized.startswith(("remind me to ", "todo:", "task:", "deadline:")):
                return self._task_application.propose(event.text or "", chat_id=event.chat_id)
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
            intake_followup = self._provisional_intake_application.handle_followup(event)
            if intake_followup is not None:
                return intake_followup
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
            if self._tool_agent_application is not None:
                tool_response = self._tool_agent_application.handle_request(event)
                if tool_response is not None:
                    return tool_response
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
            return PresentedReply(
                f"{saved}\n\n{proposal.text}", proposal.actions,
                title=proposal.title, icon=proposal.icon,
            )
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
