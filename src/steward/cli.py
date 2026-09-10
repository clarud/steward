"""Command-line entry point for Steward."""

from __future__ import annotations

import argparse
import logging
import os
import re
import sys
import sqlite3
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

from steward.config import Settings, load_environment_file
from steward.application import (
    StewardActionProposalApplication,
    StewardCaptureApplication,
    StewardDriveImportApplication,
    StewardGmailImportApplication,
    StewardEventApplication,
    StewardOrganizationApprovalApplication,
    StewardQuestionApplication,
    StewardReadApplication,
    StewardReviewInboxApplication,
    StewardProvisionalIntakeApplication,
    StewardToolAgentApplication,
    StewardRecordApplication,
    StewardKnowledgeApplication,
    StewardRootsApplication,
    StewardPrivacyApplication,
    StewardOperationsApplication,
    StewardCalendarApplication,
    StewardTaskApplication,
    StewardResearchApplication,
    StewardCuratedNoteApplication,
    StewardWorkspaceLinkApplication,
    StewardIntegrationStatusApplication,
)
from steward.capture import InboxCaptureService
from steward.reviews import ReviewContextRepository
from steward.answer import (
    AnswerService,
    ContextBuilder,
    GeminiModelGateway,
    ModelRouter,
    ModelGateway,
    OllamaModelGateway,
    OpenAIModelGateway,
)
from steward.extraction import DocumentExtractionError, MarkdownExtractor, SourceFragmentRepository
from steward.graphs import build_organization_approval_graph, build_retrieval_answer_graph
from steward.graphs import (
    GeminiToolCallingModel,
    OllamaToolCallingModel,
    OpenAICompatibleToolCallingModel,
    build_tool_agent_graph,
)
from steward.logging import configure_logging
from steward.sources import SourceRepository, SourceType
from steward.sources.service import SourceService
from steward.storage import initialize_database, restore_database, snapshot_database
from steward.retrieval import (
    HybridRetriever,
    LexicalSearchService,
    SemanticSearchService,
    SentenceTransformerEmbeddingProvider,
    SQLiteSemanticIndex,
)
from steward.telegram import (
    TelegramCallbackRepository,
    TelegramUpdateDeliveryRepository,
    run_telegram_polling,
)
from steward.workspaces import WorkspaceRepository, WorkspaceService
from steward.organization import (
    OrganizationApprovalService,
    OrganizationApprovalThreadRepository,
    OrganizationProposalRepository,
    OrganizationService,
)
from steward.organization_ai import ModelAssistedOrganizationService
from steward.activity import ActivityService, ActivityType
from steward.actions import FileMutationService
from steward.action_proposals import ActionProposalRepository, ActionProposalService
from steward.intake import ProvisionalIntakeRepository, ProvisionalIntakeService
from steward.roots import SourceRootRepository
from steward.records import RecordService
from steward.tasks import TaskReminderService, TaskService
from steward.calendar import (
    CalendarEventProposalService,
    CalendarService,
    CalendarWriteService,
    GOOGLE_CALENDAR_EVENTS_SCOPE,
    authorize_google_calendar,
)
from steward.drive import DriveInboxImportService, GoogleDriveService, authorize_google_drive
from steward.gmail import GmailInboxImportService, GmailService, authorize_gmail
from steward.evaluation import evaluate_lexical_retrieval, load_retrieval_cases
from steward.research import (
    DuckDuckGoSearchProvider,
    GeminiGoogleSearchProvider,
    ResearchProvider,
    ResearchProviderError,
    ResearchRetentionService,
    ResearchService,
)
from steward.workspace_detection import WorkspaceDetectionService
from steward.web_ui import LocalRecordBrowser, LocalSourceBrowser, run_local_ui
from steward.knowledge_connector import KnowledgeConnector
from steward.file_watching import run_file_watcher
from steward.privacy import PrivacyRule, PrivacyService
from steward.knowledge import KnowledgeEnrichmentProposalRepository, KnowledgeService
from steward.knowledge_ai import ModelAssistedKnowledgeService
from steward.tools import (
    ACTION_PROPOSAL_TOOL_DEFINITIONS,
    ActionProposalToolService,
    CALENDAR_PROPOSAL_TOOL_DEFINITIONS,
    CalendarReadToolService,
    CalendarProposalToolService,
    ReadOnlyToolService,
    ToolPolicy,
    build_action_proposal_tools,
    build_calendar_proposal_tools,
    build_calendar_read_tools,
    build_read_only_tools,
    KNOWLEDGE_PROPOSAL_TOOL_DEFINITIONS,
    KnowledgeProposalToolService,
    build_knowledge_proposal_tools,
)
from steward.tools.read_only import READ_ONLY_TOOL_DEFINITIONS
from steward.tools.calendar_read import CALENDAR_READ_TOOL_DEFINITIONS
from langgraph.checkpoint.sqlite import SqliteSaver
from langchain_core.messages import HumanMessage, SystemMessage


def _is_calendar_question(question: str) -> bool:
    """Recognize unambiguous schedule questions before asking a model to plan."""

    normalized = question.casefold()
    if re.search(r"\b(calendar queues?|queueing|operating systems?)\b", normalized):
        return False
    return bool(
        re.search(
            r"\b(calendar|schedule[ds]?|upcoming|coming up|today|tomorrow|this week)\b",
            normalized,
        )
    )


def _is_calendar_write_request(question: str) -> bool:
    """Keep schedule questions read-only unless the user asks for a concrete write."""

    return bool(
        re.search(r"\b(create|add|put|schedule)\b", question.casefold())
        and re.search(r"\b(calendar|event|flight)\b", question.casefold())
    )


def _configure_console_encoding() -> None:
    """Allow source text such as λ to print in legacy Windows terminals."""

    if (
        hasattr(sys.stdout, "reconfigure")
        and (sys.stdout.encoding or "").casefold().replace("-", "") != "utf8"
    ):
        sys.stdout.reconfigure(encoding="utf-8")


def _add_source_type_filter(parser: argparse.ArgumentParser) -> None:
    """Add a repeatable source-type filter to a retrieval command."""

    parser.add_argument(
        "--source-type",
        dest="source_types",
        action="append",
        choices=sorted(
            source_type.value
            for source_type in SourceType
            if source_type is not SourceType.BINARY
        ),
        help="Restrict results to one source type; repeat to include several types",
    )


def _requested_source_types(arguments: argparse.Namespace) -> tuple[SourceType, ...]:
    return tuple(SourceType(value) for value in (arguments.source_types or ()))


def _print_search_hit(hit: object) -> None:
    """Render provenance and a compact, optionally FTS-highlighted excerpt."""

    source = hit.source  # type: ignore[attr-defined]
    fragment = hit.fragment  # type: ignore[attr-defined]
    heading = fragment.heading or "Preamble"
    highlighted_text = getattr(hit, "highlighted_text", None)
    snippet = " ".join((highlighted_text or fragment.text).split())
    print(f"{source.path}:{fragment.location} [{heading}]")
    print(f"  {snippet[:160]}")


def build_parser() -> argparse.ArgumentParser:
    """Create the command-line interface for currently available features."""
    parser = argparse.ArgumentParser(prog="steward")
    subcommands = parser.add_subparsers(dest="command")
    scan_parser = subcommands.add_parser("scan", help="Register supported source files under a root")
    scan_parser.add_argument("root", type=Path, help="Directory containing supported source files")
    watch_parser = subcommands.add_parser("watch", help="Watch a Markdown vault and incrementally refresh changed files")
    watch_parser.add_argument("root", type=Path)
    watch_root_parser = subcommands.add_parser("watch-root", help="Watch one locally authorized source root")
    watch_root_parser.add_argument("name", help="Authorized source-root name")
    index_parser = subcommands.add_parser(
        "index", help="Scan supported source files and build their local semantic index"
    )
    index_parser.add_argument("root", type=Path, help="Directory containing supported source files")
    reextract_parser = subcommands.add_parser(
        "reextract", help="Explicitly rebuild one source's derived text and semantic vectors"
    )
    reextract_parser.add_argument("source_id", type=int)
    subcommands.add_parser(
        "rebuild-semantic-index",
        help="Regenerate local derived vectors from existing extracted fragments",
    )
    evaluation_parser = subcommands.add_parser(
        "evaluate-retrieval", help="Measure lexical retrieval against human-authored expected results"
    )
    evaluation_parser.add_argument("root", type=Path, help="The already indexed vault root")
    evaluation_parser.add_argument("cases", type=Path, help="YAML cases with query and expected source/heading")
    evaluation_parser.add_argument("--mode", choices=("lexical", "hybrid"), default="lexical")
    subcommands.add_parser(
        "download-embedding-model",
        help="Download Steward's local embedding model for semantic search",
    )
    subcommands.add_parser("sources", help="List registered sources")
    backup_parser = subcommands.add_parser(
        "backup", help="Create consistent local snapshots of Steward's SQLite databases"
    )
    backup_parser.add_argument(
        "--destination", type=Path,
        help="New directory for snapshots (defaults to DATA_DIR/backups/<timestamp>)",
    )
    restore_parser = subcommands.add_parser(
        "restore", help="Restore one local SQLite database from a snapshot after creating a safety backup"
    )
    restore_parser.add_argument("--snapshot", type=Path, required=True, help="Existing SQLite snapshot to restore")
    restore_parser.add_argument("--destination", type=Path, required=True, help="Existing active database to replace")
    restore_parser.add_argument("--safety-backup", type=Path, required=True, help="New path for a safety snapshot of the active database")
    restore_parser.add_argument("--confirm", action="store_true", help="Confirm that Steward is stopped and the database will be replaced")
    root_add = subcommands.add_parser("add-root", help="Locally authorize an existing directory as a source root")
    root_add.add_argument("name")
    root_add.add_argument("path", type=Path)
    root_add.add_argument(
        "--exclude", action="append", type=Path, default=[],
        help="Root-relative directory to exclude (repeatable)",
    )
    subcommands.add_parser("roots", help="List locally authorized source roots")
    subcommands.add_parser(
        "health", help="Report safe local runtime health without exposing paths or secrets"
    )
    scan_root_parser = subcommands.add_parser("scan-root", help="Scan one locally authorized source root")
    scan_root_parser.add_argument("name", help="Authorized source-root name")
    for command, help_text in (("enable-root", "Enable a locally authorized source root"), ("disable-root", "Disable a locally authorized source root")):
        root_toggle = subcommands.add_parser(command, help=help_text)
        root_toggle.add_argument("name", help="Authorized source-root name")
    unregister_source_parser = subcommands.add_parser(
        "unregister-source",
        help="Remove a source from Steward's registry without deleting its original file",
    )
    unregister_source_parser.add_argument("source_id", type=int)
    unregister_source_parser.add_argument(
        "--confirm",
        action="store_true",
        help="Confirm removal of local metadata and derived indexes",
    )
    search_parser = subcommands.add_parser("search", help="Search indexed source fragments")
    search_parser.add_argument("query", help="Terms to search for")
    search_parser.add_argument("--limit", type=int, default=5, help="Maximum matches")
    _add_source_type_filter(search_parser)
    search_parser.add_argument("--path-prefix", type=Path, help="Restrict matches to a source-path subtree")
    semantic_parser = subcommands.add_parser(
        "semantic-search", help="Search Markdown fragments by meaning"
    )
    semantic_parser.add_argument("query", help="A natural-language question or phrase")
    semantic_parser.add_argument("--limit", type=int, default=5, help="Maximum matches")
    _add_source_type_filter(semantic_parser)
    hybrid_parser = subcommands.add_parser(
        "hybrid-search", help="Combine lexical and semantic Markdown search"
    )
    hybrid_parser.add_argument("query", help="Terms or a natural-language question")
    hybrid_parser.add_argument("--limit", type=int, default=5, help="Maximum matches")
    _add_source_type_filter(hybrid_parser)
    ask_parser = subcommands.add_parser(
        "ask", help="Answer a question from retrieved local source fragments"
    )
    ask_parser.add_argument("question", help="Question to answer from local evidence")
    ask_parser.add_argument("--limit", type=int, default=5, help="Maximum evidence fragments")
    ask_parser.add_argument(
        "--thread-id",
        default="cli:ask",
        help="Persistent LangGraph conversation thread ID",
    )
    agent_parser = subcommands.add_parser(
        "agent", help="Answer using Steward tools and create reviewable action proposals"
    )
    agent_parser.add_argument("question", help="Question or safe action request for the agent")
    agent_parser.add_argument("--thread-id", default="cli:agent", help="Persistent LangGraph thread ID")
    agent_parser.add_argument("--include-calendar", action="store_true", help="Allow current Google Calendar read tools after OAuth")
    research_parser = subcommands.add_parser("research", help="Research externally without retaining the sources")
    research_parser.add_argument("question")
    research_parser.add_argument("--provider", choices=("auto", "gemini", "duckduckgo"), default="auto")
    retain_research_parser = subcommands.add_parser(
        "research-retain", help="Research externally and explicitly retain a provenance-labeled Inbox note"
    )
    retain_research_parser.add_argument("question")
    retain_research_parser.add_argument("--provider", choices=("auto", "gemini", "duckduckgo"), default="auto")
    telegram_parser = subcommands.add_parser(
        "telegram", help="Run the local Telegram adapter with long polling"
    )
    telegram_parser.add_argument(
        "--limit", type=int, default=5, help="Maximum evidence fragments per question"
    )
    telegram_deliveries = subcommands.add_parser(
        "telegram-deliveries", help="Inspect local Telegram delivery coordination state"
    )
    telegram_deliveries.add_argument("--limit", type=int, default=20)
    telegram_delivery_history = subcommands.add_parser(
        "telegram-delivery-history", help="Inspect metadata-only Telegram retry history"
    )
    telegram_delivery_history.add_argument("--limit", type=int, default=50)
    telegram_dead_letters = subcommands.add_parser(
        "telegram-dead-letters", help="Inspect terminal metadata-only Telegram delivery failures"
    )
    telegram_dead_letters.add_argument("--limit", type=int, default=50)
    telegram_recover_dead_letter = subcommands.add_parser(
        "telegram-recover-dead-letter",
        help="Reopen a dead-letter retry budget for a future genuine Telegram redelivery",
    )
    telegram_recover_dead_letter.add_argument("update_id")
    telegram_recover_dead_letter.add_argument(
        "--confirm",
        action="store_true",
        help="Confirm that this does not replay the unavailable original message",
    )
    ui_parser = subcommands.add_parser("ui", help="Run the localhost-only local search UI")
    ui_parser.add_argument("--host", default="127.0.0.1")
    ui_parser.add_argument("--port", type=int, default=8765)
    ui_parser.add_argument("--mode", choices=("lexical", "hybrid"), default="lexical")
    workspace_parser = subcommands.add_parser("create-workspace", help="Create an explicit workspace")
    workspace_parser.add_argument("name")
    subcommands.add_parser("workspaces", help="List workspaces")
    subcommands.add_parser("review-inbox-workspaces", help="Propose possible new workspaces from Inbox sources")
    subcommands.add_parser("connect-knowledge", help="Propose evidence-backed connections between concepts")
    enrichment_parser = subcommands.add_parser(
        "propose-knowledge-enrichment", help="Compare a claim with one evidence fragment without changing knowledge"
    )
    enrichment_parser.add_argument("claim_id", type=int)
    enrichment_parser.add_argument("fragment_id", type=int)
    enrichment_parser.add_argument("--model-assisted", action="store_true")
    subcommands.add_parser(
        "knowledge-enrichment-proposals",
        help="List durable, evidence-backed knowledge enrichment proposals",
    )
    review_enrichment_parser = subcommands.add_parser(
        "review-knowledge-enrichment",
        help="Accept or reject one knowledge enrichment proposal without rewriting a claim",
    )
    review_enrichment_parser.add_argument("proposal_id", type=int)
    review_enrichment_parser.add_argument("status", choices=("accepted", "rejected"))
    privacy_parser = subcommands.add_parser("set-source-privacy", help="Set a source's model privacy rule")
    privacy_parser.add_argument("source_id", type=int)
    privacy_parser.add_argument("rule", choices=[rule.value for rule in PrivacyRule])
    source_privacy_parser = subcommands.add_parser("source-privacy", help="Show a source's privacy rule")
    source_privacy_parser.add_argument("source_id", type=int)
    link_parser = subcommands.add_parser("link-source", help="Relate a source to a workspace")
    link_parser.add_argument("workspace_id", type=int)
    link_parser.add_argument("source_id", type=int)
    propose_parser = subcommands.add_parser("propose-organization", help="Create a non-mutating organization proposal")
    propose_parser.add_argument("source_id", type=int)
    propose_parser.add_argument(
        "--model-assisted",
        action="store_true",
        help="Use the configured model to select only among existing workspaces",
    )
    subcommands.add_parser("organization-proposals", help="List organization proposals")
    subcommands.add_parser("activity", help="List recent activity events")
    review_parser = subcommands.add_parser("review-proposal", help="Accept or reject an organization proposal")
    review_parser.add_argument("proposal_id", type=int)
    review_parser.add_argument("status", choices=("accepted", "rejected"))
    subcommands.add_parser("action-proposals", help="List pending and reviewed agent action proposals")
    action_review_parser = subcommands.add_parser(
        "review-action-proposal", help="Accept or reject an agent action proposal"
    )
    action_review_parser.add_argument("proposal_id", type=int)
    action_review_parser.add_argument("status", choices=("accepted", "rejected"))
    travel_parser = subcommands.add_parser("propose-travel-record", help="Interpret source fragments as a travel record")
    travel_parser.add_argument("source_id", type=int)
    create_travel_parser = subcommands.add_parser(
        "create-travel-record", help="Persist an evidence-backed travel record proposed from a source"
    )
    create_travel_parser.add_argument("source_id", type=int)
    subcommands.add_parser("travel-records", help="List saved travel records")
    receipt_parser = subcommands.add_parser("propose-receipt-record", help="Interpret source fragments as a receipt record")
    receipt_parser.add_argument("source_id", type=int)
    create_receipt_parser = subcommands.add_parser(
        "create-receipt-record", help="Persist an evidence-backed receipt record proposed from a source"
    )
    create_receipt_parser.add_argument("source_id", type=int)
    subcommands.add_parser("receipt-records", help="List saved receipt records")
    warranty_parser = subcommands.add_parser("propose-warranty-record", help="Interpret source fragments as a warranty record")
    warranty_parser.add_argument("source_id", type=int)
    create_warranty_parser = subcommands.add_parser("create-warranty-record", help="Persist an evidence-backed warranty record proposed from a source")
    create_warranty_parser.add_argument("source_id", type=int)
    subcommands.add_parser("warranty-records", help="List saved warranty records")
    travel_references = subcommands.add_parser(
        "travel-record-references", help="List source-backed references for a travel record"
    )
    travel_references.add_argument("record_id", type=int)
    add_travel_reference = subcommands.add_parser(
        "add-travel-record-reference", help="Add a source-backed reference to a travel record"
    )
    add_travel_reference.add_argument("record_id", type=int)
    add_travel_reference.add_argument("reference_type")
    add_travel_reference.add_argument("value")
    add_travel_reference.add_argument("fragment_id", type=int)
    calendar_authorize = subcommands.add_parser(
        "calendar-authorize", help="Authorize local read-only Google Calendar access"
    )
    calendar_authorize.add_argument("client_secrets", type=Path, help="Google OAuth desktop-client JSON file")
    calendar_authorize.add_argument("--token-file", type=Path)
    calendar_search = subcommands.add_parser("calendar-search", help="Search current Google Calendar events")
    calendar_search.add_argument("query", nargs="?", default="")
    calendar_search.add_argument("--after", type=datetime.fromisoformat)
    calendar_search.add_argument("--before", type=datetime.fromisoformat)
    calendar_search.add_argument("--limit", type=int, default=10)
    calendar_search.add_argument("--client-secrets", type=Path)
    calendar_get = subcommands.add_parser("calendar-get", help="Read one current Google Calendar event")
    calendar_get.add_argument("event_id")
    calendar_get.add_argument("--client-secrets", type=Path)
    calendar_create = subcommands.add_parser(
        "calendar-create-travel-event", help="Create an approved Google Calendar event from a travel record"
    )
    calendar_create.add_argument("record_id", type=int)
    calendar_create.add_argument("--client-secrets", type=Path)
    calendar_propose = subcommands.add_parser(
        "calendar-propose-travel-event", help="Create a pending Calendar-event proposal for a travel record"
    )
    calendar_propose.add_argument("record_id", type=int)
    calendar_review = subcommands.add_parser(
        "calendar-review-travel-event", help="Accept or reject a pending Calendar-event proposal"
    )
    calendar_review.add_argument("proposal_id", type=int)
    calendar_review.add_argument("status", choices=("accepted", "rejected"))
    calendar_review.add_argument("--client-secrets", type=Path)
    drive_authorize = subcommands.add_parser("drive-authorize", help="Authorize local read-only Google Drive access")
    drive_authorize.add_argument("client_secrets", type=Path, help="Google OAuth desktop-client JSON file")
    drive_authorize.add_argument("--token-file", type=Path)
    drive_search = subcommands.add_parser("drive-search", help="Search current Google Drive file metadata")
    drive_search.add_argument("query", nargs="?", default="")
    drive_search.add_argument("--limit", type=int, default=10)
    drive_search.add_argument("--client-secrets", type=Path)
    drive_import = subcommands.add_parser("drive-import", help="Explicitly import one selected Drive original into Inbox")
    drive_import.add_argument("file_id")
    drive_import.add_argument("--client-secrets", type=Path)
    gmail_authorize = subcommands.add_parser("gmail-authorize", help="Authorize local read-only Gmail access")
    gmail_authorize.add_argument("client_secrets", type=Path, help="Google OAuth desktop-client JSON file")
    gmail_authorize.add_argument("--token-file", type=Path)
    gmail_search = subcommands.add_parser("gmail-search", help="Search current Gmail message metadata")
    gmail_search.add_argument("query", nargs="?", default="")
    gmail_search.add_argument("--limit", type=int, default=10)
    gmail_search.add_argument("--client-secrets", type=Path)
    gmail_import = subcommands.add_parser("gmail-import", help="Explicitly import one selected Gmail message into Inbox")
    gmail_import.add_argument("message_id")
    gmail_import.add_argument("--client-secrets", type=Path)
    return parser


def _model_gateway_from_settings(
    settings: Settings, *, command: str
) -> ModelGateway | None:
    """Create the configured model gateway, or print its actionable setup error."""

    if settings.model_provider == "local":
        if not settings.local_model:
            print(
                "Set STEWARD_LOCAL_MODEL before using "
                f"`steward {command}` with STEWARD_MODEL_PROVIDER=local."
            )
            return None
        return OllamaModelGateway(
            model=settings.local_model, base_url=settings.local_model_url
        )

    if settings.model_provider == "gemini":
        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key or not settings.gemini_model:
            print(
                "Set GEMINI_API_KEY and STEWARD_GEMINI_MODEL before using "
                f"`steward {command}`."
            )
            return None
        return GeminiModelGateway(api_key=api_key, model=settings.gemini_model)

    if settings.model_provider == "soclaas":
        api_key = os.environ.get("STEWARD_SOCLAAS_API_KEY") or os.environ.get("SOCLAAS_API_KEY")
        if not api_key or not settings.soclaas_model or not settings.soclaas_base_url:
            print(
                "Set SOCLAAS_API_KEY, SOCLAAS_MODEL, and SOCLAAS_BASE_URL "
                f"before using `steward {command}`."
            )
            return None
        return OpenAIModelGateway(
            api_key=api_key, model=settings.soclaas_model, base_url=settings.soclaas_base_url
        )

    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key or not settings.openai_model:
        print(
            "Set OPENAI_API_KEY and STEWARD_OPENAI_MODEL before using "
            f"`steward {command}`."
        )
        return None
    return OpenAIModelGateway(api_key=api_key, model=settings.openai_model)


def _research_provider_from_settings(settings: Settings, provider_name: str) -> ResearchProvider | None:
    """Select web research separately from the model used for local answers."""
    if provider_name == "duckduckgo":
        return DuckDuckGoSearchProvider()
    api_key = os.environ.get("GEMINI_API_KEY")
    if provider_name == "gemini":
        if not api_key or not settings.gemini_model:
            print("Set GEMINI_API_KEY and STEWARD_GEMINI_MODEL before using Gemini research.")
            return None
        return GeminiGoogleSearchProvider(api_key=api_key, model=settings.gemini_model)
    if api_key and settings.gemini_model:
        return GeminiGoogleSearchProvider(api_key=api_key, model=settings.gemini_model)
    return DuckDuckGoSearchProvider()


def _tool_calling_model_from_settings(settings: Settings):
    """Build the provider adapter needed specifically by the agent tool loop."""

    if settings.model_provider == "local":
        if not settings.local_model:
            print(
                "Set STEWARD_LOCAL_MODEL before using `steward agent` "
                "with STEWARD_MODEL_PROVIDER=local."
            )
            return None
        return OllamaToolCallingModel(
            model=settings.local_model, base_url=settings.local_model_url
        )
    if settings.model_provider == "gemini":
        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key or not settings.gemini_model:
            print("Set GEMINI_API_KEY and STEWARD_GEMINI_MODEL before using `steward agent`.")
            return None
        return GeminiToolCallingModel(api_key=api_key, model=settings.gemini_model)

    if settings.model_provider == "soclaas":
        api_key = os.environ.get("STEWARD_SOCLAAS_API_KEY") or os.environ.get("SOCLAAS_API_KEY")
        if not api_key or not settings.soclaas_model or not settings.soclaas_base_url:
            print(
                "Set SOCLAAS_API_KEY, SOCLAAS_MODEL, and SOCLAAS_BASE_URL "
                "before using `steward agent`."
            )
            return None
        return OpenAICompatibleToolCallingModel(
            api_key=api_key, model=settings.soclaas_model, base_url=settings.soclaas_base_url,
        )

    print("`steward agent` currently supports Gemini, local Ollama, or SoCLaaS.")
    return None


class _ConfiguredDriveInboxImporter:
    """Authorize Drive lazily so starting Telegram never opens a browser."""

    def __init__(self, settings: Settings, capture_service: InboxCaptureService) -> None:
        self._settings = settings
        self._capture_service = capture_service

    def import_file(self, file_id: str):
        configured = os.environ.get("STEWARD_GOOGLE_CLIENT_SECRETS")
        if not configured:
            raise ValueError("Set STEWARD_GOOGLE_CLIENT_SECRETS before importing from Drive.")
        drive = GoogleDriveService(
            authorize_google_drive(
                Path(configured), self._settings.data_dir / "config" / "google-drive-token.json"
            )
        )
        return DriveInboxImportService(drive, self._capture_service).import_file(file_id)

    def search(self, query: str):
        configured = os.environ.get("STEWARD_GOOGLE_CLIENT_SECRETS")
        if not configured:
            raise ValueError("Set STEWARD_GOOGLE_CLIENT_SECRETS before searching Drive.")
        return GoogleDriveService(
            authorize_google_drive(Path(configured), self._settings.data_dir / "config" / "google-drive-token.json")
        ).search(query)


def _drive_inbox_importer(
    settings: Settings, capture_service: InboxCaptureService
) -> _ConfiguredDriveInboxImporter | None:
    """Expose Drive relay only when this local process has an OAuth client configured."""

    if not os.environ.get("STEWARD_GOOGLE_CLIENT_SECRETS"):
        return None
    return _ConfiguredDriveInboxImporter(settings, capture_service)


class _ConfiguredGmailInboxImporter:
    """Authorize Gmail lazily, only after an explicit Telegram import command."""

    def __init__(self, settings: Settings, capture_service: InboxCaptureService) -> None:
        self._settings = settings
        self._capture_service = capture_service

    def import_message(self, message_id: str):
        configured = os.environ.get("STEWARD_GOOGLE_CLIENT_SECRETS")
        if not configured:
            raise ValueError("Set STEWARD_GOOGLE_CLIENT_SECRETS before importing from Gmail.")
        gmail = GmailService(
            authorize_gmail(
                Path(configured), self._settings.data_dir / "config" / "gmail-token.json"
            )
        )
        return GmailInboxImportService(gmail, self._capture_service).import_message(message_id)

    def search(self, query: str):
        configured = os.environ.get("STEWARD_GOOGLE_CLIENT_SECRETS")
        if not configured:
            raise ValueError("Set STEWARD_GOOGLE_CLIENT_SECRETS before searching Gmail.")
        return GmailService(
            authorize_gmail(Path(configured), self._settings.data_dir / "config" / "gmail-token.json")
        ).search(query)


def _gmail_inbox_importer(
    settings: Settings, capture_service: InboxCaptureService
) -> _ConfiguredGmailInboxImporter | None:
    if not os.environ.get("STEWARD_GOOGLE_CLIENT_SECRETS"):
        return None
    return _ConfiguredGmailInboxImporter(settings, capture_service)


def _calendar_writer_factory(settings: Settings, database_path: Path):
    """Delay Calendar OAuth until an authorized human accepts a proposal."""

    def create_writer() -> CalendarWriteService:
        configured = os.environ.get("STEWARD_GOOGLE_CLIENT_SECRETS")
        if not configured:
            raise ValueError("Set STEWARD_GOOGLE_CLIENT_SECRETS before writing Calendar.")
        return CalendarWriteService(
            CalendarService(
                authorize_google_calendar(
                    Path(configured),
                    settings.data_dir / "config" / "google-calendar-token.json",
                    scopes=(GOOGLE_CALENDAR_EVENTS_SCOPE,),
                )
            ),
            database_path,
            ActivityService(database_path),
        )

    return create_writer


def _calendar_reader_factory(settings: Settings):
    """Delay local Calendar authorization until a Telegram read is requested."""

    def create_reader() -> CalendarService:
        configured = os.environ.get("STEWARD_GOOGLE_CLIENT_SECRETS")
        if not configured:
            raise ValueError("Set STEWARD_GOOGLE_CLIENT_SECRETS before reading Calendar.")
        return CalendarService(
            authorize_google_calendar(
                Path(configured), settings.data_dir / "config" / "google-calendar-token.json"
            )
        )

    return create_reader


def _build_question_graph(
    settings: Settings,
    model_gateway: ModelGateway,
    *,
    limit: int,
    embedding_provider: SentenceTransformerEmbeddingProvider | None = None,
):
    """Compose the reusable local retrieval-and-answer workflow."""

    database_path = settings.data_dir / "steward.db"
    initialize_database(database_path)
    source_repository = SourceRepository(database_path)
    fragment_repository = SourceFragmentRepository(database_path)
    embedding_provider = embedding_provider or SentenceTransformerEmbeddingProvider()
    semantic_search = SemanticSearchService(
        source_repository,
        SQLiteSemanticIndex(database_path, embedding_provider),
    )
    retriever = HybridRetriever(
        LexicalSearchService(source_repository, fragment_repository), semantic_search
    )
    local_gateway = (
        OllamaModelGateway(model=settings.local_model, base_url=settings.local_model_url)
        if settings.local_model
        else None
    )
    privacy = PrivacyService(database_path)
    answer_service = AnswerService(
        retriever=retriever,
        context_builder=ContextBuilder(),
        model_gateway=model_gateway,
        privacy_service=privacy,
        model_router=ModelRouter(privacy, model_gateway, local_gateway),
    )
    checkpoint_connection = sqlite3.connect(
        settings.data_dir / "checkpoints.db", check_same_thread=False
    )
    checkpointer = SqliteSaver(checkpoint_connection)
    checkpointer.setup()
    return build_retrieval_answer_graph(
        retriever, answer_service, retrieval_limit=limit, checkpointer=checkpointer
    )


def _print_scan_database_error(operation: str, error: sqlite3.Error) -> None:
    """Give local operators an actionable scan failure without hiding safety facts."""
    message = str(error).casefold()
    if "locked" in message or "busy" in message:
        print(
            f"{operation} stopped: the local Steward database is busy. "
            "Wait for the other local Steward operation to finish, then retry. "
            "Original files were not changed."
        )
        return
    print(f"{operation} stopped because the local Steward database reported an error: {error}")


def _database_health(database_path: Path) -> str:
    """Read a database without creating it, because health checks must be non-mutating."""
    if not database_path.is_file():
        return "not initialized"
    try:
        with sqlite3.connect(f"{database_path.resolve().as_uri()}?mode=ro", uri=True) as connection:
            connection.execute("SELECT 1").fetchone()
    except sqlite3.Error as error:
        message = str(error).casefold()
        return "busy" if "locked" in message or "busy" in message else "unavailable"
    return "available"


def _health_report(settings: Settings) -> str:
    """Render only operator-safe local health metadata."""
    database_path = settings.data_dir / "steward.db"
    checkpoint_path = settings.data_dir / "checkpoints.db"
    database_health = _database_health(database_path)
    checkpoint_health = _database_health(checkpoint_path)
    root_summary = "not initialized"
    if database_health == "available":
        try:
            roots = SourceRootRepository(database_path).list_all()
        except sqlite3.Error:
            root_summary = "unavailable"
        else:
            available = sum(root.health == "available" for root in roots)
            missing = sum(root.health == "missing" for root in roots)
            disabled = sum(root.health == "disabled" for root in roots)
            root_summary = f"{available} available, {missing} missing, {disabled} disabled"
    # Keep this aligned with the variable read by the ``telegram`` command.
    telegram = "configured" if os.environ.get("TELEGRAM_BOT_TOKEN") else "not configured"
    return (
        "Steward health:\n"
        f"Operational database: {database_health}\n"
        f"Conversation checkpoints: {checkpoint_health}\n"
        f"Authorized roots: {root_summary}\n"
        f"Telegram token: {telegram}"
    )


def main(argv: Sequence[str] | None = None) -> None:
    """Run a Steward command."""
    _configure_console_encoding()
    load_environment_file()
    arguments = build_parser().parse_args(argv)
    settings = Settings.from_environment()
    configure_logging(settings)

    if arguments.command == "health":
        print(_health_report(settings))
        return

    if arguments.command == "backup":
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        destination = arguments.destination or settings.data_dir / "backups" / timestamp
        if destination.exists():
            print(f"Backup destination already exists: {destination.resolve()}")
            return
        database_paths = (settings.data_dir / "steward.db", settings.data_dir / "checkpoints.db")
        available = tuple(path for path in database_paths if path.is_file())
        if not available:
            print("No Steward databases exist yet; there is nothing to back up.")
            return
        try:
            snapshots = tuple(snapshot_database(path, destination / path.name) for path in available)
        except (OSError, ValueError, sqlite3.Error) as error:
            print(f"Backup failed: {error}")
            return
        print("Backed up local Steward databases:\n" + "\n".join(str(path) for path in snapshots))
        return

    if arguments.command == "restore":
        if not arguments.confirm:
            print("Refusing to restore without --confirm. Stop Steward first; restore replaces the active database after creating its safety backup.")
            return
        try:
            safety_backup = restore_database(
                arguments.snapshot, arguments.destination, arguments.safety_backup
            )
        except (OSError, ValueError, sqlite3.Error) as error:
            print(f"Restore failed: {error}")
            return
        print(f"Restored {arguments.destination.resolve()} from snapshot. Safety backup: {safety_backup}")
        return

    if arguments.command == "scan":
        database_path = settings.data_dir / "steward.db"
        try:
            initialize_database(database_path)
            result = SourceService(
                source_repository=SourceRepository(database_path),
                fragment_repository=SourceFragmentRepository(database_path),
                markdown_extractor=MarkdownExtractor(),
            ).scan_source_root(arguments.root)
        except sqlite3.Error as error:
            _print_scan_database_error("Scan", error)
            return
        print(
            "Scan complete: "
            f"new={result.new} updated={result.updated} "
            f"unchanged={result.unchanged} missing={result.missing}"
        )
        return

    if arguments.command == "scan-root":
        database_path = settings.data_dir / "steward.db"
        try:
            initialize_database(database_path)
            root = SourceRootRepository(database_path).get_by_name(arguments.name)
            if root is None:
                print(f"No locally authorized source root named {arguments.name!r}.")
                return
            if not root.enabled:
                print(f"Source root {root.name!r} is disabled.")
                return
            if not root.path.is_dir():
                print(f"Source root {root.name!r} is unavailable: {root.path}")
                return
            result = SourceService(
                source_repository=SourceRepository(database_path),
                fragment_repository=SourceFragmentRepository(database_path),
                markdown_extractor=MarkdownExtractor(),
            ).scan_source_root(root.path, exclusions=root.exclusions)
        except sqlite3.Error as error:
            _print_scan_database_error("Root scan", error)
            return
        print(
            f"Scan complete for {root.name}: "
            f"new={result.new} updated={result.updated} "
            f"unchanged={result.unchanged} missing={result.missing}"
        )
        return

    if arguments.command in {"enable-root", "disable-root"}:
        database_path = settings.data_dir / "steward.db"
        initialize_database(database_path)
        enabled = arguments.command == "enable-root"
        try:
            root = SourceRootRepository(database_path).set_enabled(arguments.name, enabled)
        except ValueError as error:
            print(str(error))
            return
        print(f"Source root {root.name!r} is now {'enabled' if root.enabled else 'disabled'}.")
        return

    if arguments.command == "watch":
        database_path = settings.data_dir / "steward.db"
        initialize_database(database_path)
        service = SourceService(SourceRepository(database_path), SourceFragmentRepository(database_path), MarkdownExtractor())
        print("Watching for Markdown changes. Press Ctrl+C to stop.")
        run_file_watcher(arguments.root, service)
        return

    if arguments.command == "watch-root":
        database_path = settings.data_dir / "steward.db"
        initialize_database(database_path)
        root = SourceRootRepository(database_path).get_by_name(arguments.name)
        if root is None:
            print(f"No locally authorized source root named {arguments.name!r}.")
            return
        if not root.enabled:
            print(f"Source root {root.name!r} is disabled.")
            return
        if not root.path.is_dir():
            print(f"Source root {root.name!r} is unavailable: {root.path}")
            return
        service = SourceService(SourceRepository(database_path), SourceFragmentRepository(database_path), MarkdownExtractor())
        print(f"Watching source root {root.name}. Press Ctrl+C to stop.")
        run_file_watcher(root.path, service, exclusions=root.exclusions)
        return

    if arguments.command == "index":
        database_path = settings.data_dir / "steward.db"
        initialize_database(database_path)
        provider = SentenceTransformerEmbeddingProvider()
        result = SourceService(
            source_repository=SourceRepository(database_path),
            fragment_repository=SourceFragmentRepository(database_path),
            markdown_extractor=MarkdownExtractor(),
            semantic_index=SQLiteSemanticIndex(database_path, provider),
        ).scan_source_root(arguments.root)
        print(
            "Index complete: "
            f"new={result.new} updated={result.updated} "
            f"unchanged={result.unchanged} missing={result.missing}"
        )
        return

    if arguments.command == "reextract":
        database_path = settings.data_dir / "steward.db"
        initialize_database(database_path)
        source_repository = SourceRepository(database_path)
        if source_repository.get_by_id(arguments.source_id) is None:
            print(f"Source {arguments.source_id} was not found.")
            return
        try:
            fragments = SourceService(
                source_repository=source_repository,
                fragment_repository=SourceFragmentRepository(database_path),
                markdown_extractor=MarkdownExtractor(),
                semantic_index=SQLiteSemanticIndex(
                    database_path, SentenceTransformerEmbeddingProvider()
                ),
            ).reextract_source(arguments.source_id)
        except (OSError, UnicodeDecodeError, DocumentExtractionError, ValueError) as error:
            print(str(error))
            return
        print(f"Re-extracted source {arguments.source_id}: {len(fragments)} fragment(s).")
        return

    if arguments.command == "rebuild-semantic-index":
        database_path = settings.data_dir / "steward.db"
        initialize_database(database_path)
        try:
            provider = SentenceTransformerEmbeddingProvider()
            service = SourceService(
                SourceRepository(database_path),
                SourceFragmentRepository(database_path),
                MarkdownExtractor(),
                semantic_index=SQLiteSemanticIndex(database_path, provider),
            )
            count = service.rebuild_semantic_index()
        except OSError as error:
            print(f"Could not load the local embedding model: {error}")
            return
        print(f"Rebuilt {count} semantic vectors from existing fragments.")
        return

    if arguments.command == "download-embedding-model":
        SentenceTransformerEmbeddingProvider(allow_download=True)
        print("Embedding model downloaded and ready for offline use.")
        return

    if arguments.command == "evaluate-retrieval":
        if not arguments.root.is_dir():
            print(f"Vault root does not exist or is not a directory: {arguments.root}")
            return
        if not arguments.cases.is_file():
            print(f"Retrieval case file does not exist: {arguments.cases}")
            return
        database_path = settings.data_dir / "steward.db"
        initialize_database(database_path)
        try:
            sources = SourceRepository(database_path)
            fragments = SourceFragmentRepository(database_path)
            lexical = LexicalSearchService(sources, fragments)
            service = (
                HybridRetriever(
                    lexical,
                    SemanticSearchService(
                        sources, SQLiteSemanticIndex(database_path, SentenceTransformerEmbeddingProvider())
                    ),
                )
                if arguments.mode == "hybrid"
                else lexical
            )
            evaluation = evaluate_lexical_retrieval(
                service,
                load_retrieval_cases(arguments.cases),
                arguments.root,
            )
        except ValueError as error:
            print(f"Invalid retrieval evaluation: {error}")
            return
        print(
            f"Mode: {arguments.mode}\nCases: {evaluation.case_count}\n"
            f"Recall@5: {evaluation.recall_at_5:.1%}\n"
            f"MRR: {evaluation.mean_reciprocal_rank:.3f}"
        )
        if evaluation.misses:
            print("Misses:")
            for miss in evaluation.misses:
                print(f"- {miss.query} -> {miss.expected_source} [{miss.expected_heading}]")
        return

    if arguments.command == "sources":
        database_path = settings.data_dir / "steward.db"
        initialize_database(database_path)
        sources = SourceRepository(database_path).list_all()
        if not sources:
            print("No sources registered.")
            return

        for source in sources:
            print(f"{source.id}\t{source.status.value}\t{source.path}")
        return

    if arguments.command == "add-root":
        database_path = settings.data_dir / "steward.db"; initialize_database(database_path)
        try:
            root = SourceRootRepository(database_path).add(
                arguments.name, arguments.path, exclusions=tuple(arguments.exclude)
            )
        except ValueError as error:
            print(str(error)); return
        print(f"Authorized source root {root.id}: {root.name}\t{root.path}")
        return

    if arguments.command == "roots":
        database_path = settings.data_dir / "steward.db"; initialize_database(database_path)
        roots = SourceRootRepository(database_path).list_all()
        if not roots:
            print("No locally authorized source roots."); return
        for root in roots:
            excluded = ", ".join(str(item) for item in root.exclusions) or "none"
            print(f"{root.id}\t{root.name}\t{root.health}\t{root.path}\texcluded={excluded}")
        return

    if arguments.command == "unregister-source":
        database_path = settings.data_dir / "steward.db"
        initialize_database(database_path)
        sources = SourceRepository(database_path)
        source = sources.get_by_id(arguments.source_id)
        if source is None:
            print(f"Source {arguments.source_id} was not found.")
            return
        if not arguments.confirm:
            print(
                f"Source {source.id} at {source.path} will be unregistered. "
                "Its original file will not be deleted. Re-run with --confirm."
            )
            return
        removed = sources.unregister(arguments.source_id)
        ActivityService(database_path).record(
            ActivityType.SOURCE_UNREGISTERED,
            object_id=str(removed.id),
            details=f"Unregistered local metadata; original file retained at {removed.path}",
        )
        print(
            f"Unregistered source {removed.id}. Its original file was retained at {removed.path}."
        )
        return

    if arguments.command == "search":
        database_path = settings.data_dir / "steward.db"
        initialize_database(database_path)
        hits = LexicalSearchService(
            source_repository=SourceRepository(database_path),
            fragment_repository=SourceFragmentRepository(database_path),
        ).search(
            arguments.query,
            limit=arguments.limit,
            source_types=_requested_source_types(arguments),
            path_prefix=arguments.path_prefix,
        )
        if not hits:
            print("No matching fragments.")
            return

        for hit in hits:
            _print_search_hit(hit)
        return

    if arguments.command in {"semantic-search", "hybrid-search"}:
        database_path = settings.data_dir / "steward.db"
        initialize_database(database_path)
        source_repository = SourceRepository(database_path)
        fragment_repository = SourceFragmentRepository(database_path)
        semantic_search = SemanticSearchService(
            source_repository,
            SQLiteSemanticIndex(database_path, SentenceTransformerEmbeddingProvider()),
        )
        if arguments.command == "semantic-search":
            hits = semantic_search.search(
                arguments.query,
                limit=arguments.limit,
                source_types=_requested_source_types(arguments),
            )
        else:
            hits = HybridRetriever(
                LexicalSearchService(source_repository, fragment_repository), semantic_search
            ).search(
                arguments.query,
                limit=arguments.limit,
                source_types=_requested_source_types(arguments),
            )
        if not hits:
            print("No matching fragments. Run `steward index <vault>` first.")
            return

        for hit in hits:
            _print_search_hit(hit)
        return

    if arguments.command == "ask":
        model_gateway = _model_gateway_from_settings(settings, command="ask")
        if model_gateway is None:
            return
        graph_result = _build_question_graph(
            settings, model_gateway, limit=arguments.limit
        ).invoke(
            {"question": arguments.question},
            {"configurable": {"thread_id": arguments.thread_id}},
        )
        print(graph_result["answer"])
        citations = graph_result.get("citations", ())
        if citations:
            print("\nSources:")
            for citation in citations:
                heading = citation.heading or "Preamble"
                print(
                    f"[{citation.key}] {citation.source_path}:"
                    f"{citation.location} [{heading}]"
                )
        return

    if arguments.command == "agent":
        tool_calling_model = _tool_calling_model_from_settings(settings)
        if tool_calling_model is None:
            return
        database_path = settings.data_dir / "steward.db"
        initialize_database(database_path)
        sources = SourceRepository(database_path)
        fragments = SourceFragmentRepository(database_path)
        tool_agent_application = None
        tool_model = _tool_calling_model_from_settings(settings)
        if tool_model is not None:
            tool_checkpoint_connection = sqlite3.connect(
                settings.data_dir / "checkpoints.db", check_same_thread=False
            )
            tool_checkpointer = SqliteSaver(tool_checkpoint_connection)
            tool_checkpointer.setup()
            tool_service = ReadOnlyToolService(
                sources,
                fragments,
                LexicalSearchService(sources, fragments),
                KnowledgeService(database_path),
                RecordService(database_path),
                WorkspaceRepository(database_path),
                activity,
                PrivacyService(database_path),
                model_is_local=settings.model_provider == "local",
            )
            telegram_tools = build_read_only_tools(tool_service)
            telegram_definitions = list(READ_ONLY_TOOL_DEFINITIONS)
            calendar_token = settings.data_dir / "config" / "google-calendar-token.json"
            if os.environ.get("STEWARD_GOOGLE_CLIENT_SECRETS") and calendar_token.is_file():
                telegram_tools.extend(
                    build_calendar_read_tools(CalendarReadToolService(_calendar_reader_factory(settings)))
                )
                telegram_definitions.extend(CALENDAR_READ_TOOL_DEFINITIONS)
            tool_agent_application = StewardToolAgentApplication(
                build_tool_agent_graph(
                    tool_model,
                    telegram_tools,
                    checkpointer=tool_checkpointer,
                    tool_policy=ToolPolicy(telegram_definitions),
                )
            )
        tool_service = ReadOnlyToolService(
            sources,
            fragments,
            LexicalSearchService(sources, fragments),
            KnowledgeService(database_path),
            RecordService(database_path),
            WorkspaceRepository(database_path),
            ActivityService(database_path),
            PrivacyService(database_path),
            model_is_local=settings.model_provider == "local",
        )
        checkpoint_connection = sqlite3.connect(
            settings.data_dir / "checkpoints.db", check_same_thread=False
        )
        checkpointer = SqliteSaver(checkpoint_connection)
        checkpointer.setup()
        tools = build_read_only_tools(tool_service)
        definitions = list(READ_ONLY_TOOL_DEFINITIONS)
        action_proposals = ActionProposalService(
            ActionProposalRepository(database_path),
            WorkspaceRepository(database_path),
            ActivityService(database_path),
        )
        tools.extend(build_action_proposal_tools(ActionProposalToolService(action_proposals)))
        definitions.extend(ACTION_PROPOSAL_TOOL_DEFINITIONS)
        tools.extend(
            build_knowledge_proposal_tools(
                KnowledgeProposalToolService(
                    KnowledgeService(database_path),
                    fragments,
                    KnowledgeEnrichmentProposalRepository(database_path),
                    ActivityService(database_path),
                )
            )
        )
        definitions.extend(KNOWLEDGE_PROPOSAL_TOOL_DEFINITIONS)
        calendar_only = (
            arguments.include_calendar
            and _is_calendar_question(arguments.question)
            and not _is_calendar_write_request(arguments.question)
        )
        if arguments.include_calendar:
            client_secrets = os.environ.get("STEWARD_GOOGLE_CLIENT_SECRETS")
            if not client_secrets:
                print("Set STEWARD_GOOGLE_CLIENT_SECRETS before using --include-calendar.")
                return
            calendar = CalendarService(
                authorize_google_calendar(Path(client_secrets), settings.data_dir / "config" / "google-calendar-token.json")
            )
            calendar_tools = build_calendar_read_tools(CalendarReadToolService(calendar))
            if calendar_only:
                tools = calendar_tools
                definitions = list(CALENDAR_READ_TOOL_DEFINITIONS)
            else:
                tools.extend(calendar_tools)
                definitions.extend(CALENDAR_READ_TOOL_DEFINITIONS)
                calendar_proposals = CalendarEventProposalService(
                    ActionProposalRepository(database_path),
                    RecordService(database_path),
                    ActivityService(database_path),
                )
                tools.extend(build_calendar_proposal_tools(CalendarProposalToolService(calendar_proposals)))
                definitions.extend(CALENDAR_PROPOSAL_TOOL_DEFINITIONS)
        graph = build_tool_agent_graph(
            tool_calling_model,
            tools,
            checkpointer=checkpointer,
            tool_policy=ToolPolicy(definitions),
        )
        result = graph.invoke(
            {
                "messages": [
                    SystemMessage(
                        (
                            "You are Steward. Use the supplied read-only tools when information is needed. "
                            "This is a calendar scheduling question: call calendar_search once, then answer from "
                            "its result; do not request another tool. Do not claim a result that a tool did not provide."
                            if calendar_only
                            else "You are Steward. Use only the supplied read-only tools when information is needed. "
                            "When a tool result is sufficient, answer immediately. Never repeat a tool call with "
                            "the same arguments, and do not claim a result that a tool did not provide. "
                            "If the user explicitly asks to create a workspace, use propose_create_workspace. "
                            "If the user explicitly asks to compare a claim with retrieved evidence or save an enrichment, "
                            "use propose_knowledge_enrichment; it creates a pending review only. "
                            "It creates only a pending proposal: say that explicit approval is still required. "
                            "For an explicit request to put a saved travel record on Calendar, use "
                            "propose_create_travel_calendar_event. It never creates the event; give the "
                            "returned review command to the user."
                        )
                    ),
                    HumanMessage(arguments.question),
                ]
            },
            {"configurable": {"thread_id": arguments.thread_id}, "recursion_limit": 16},
        )
        print(str(result["messages"][-1].content))
        return

    if arguments.command in {"research", "research-retain"}:
        provider = _research_provider_from_settings(settings, arguments.provider)
        if provider is None:
            return
        try:
            bundle = ResearchService(provider).research(arguments.question)
        except ResearchProviderError as error:
            print(f"External research is temporarily unavailable: {error}")
            return
        print(bundle.answer)
        if bundle.sources:
            print("\nExternal sources (ephemeral):")
            for source in bundle.sources:
                print(f"- {source.title}: {source.url}")
        if arguments.command == "research-retain":
            database_path = settings.data_dir / "steward.db"
            initialize_database(database_path)
            capture = InboxCaptureService(
                settings.inbox_dir,
                SourceRepository(database_path),
                SourceFragmentRepository(database_path),
                ActivityService(database_path),
            )
            result = ResearchRetentionService(capture).retain(bundle)
            state = "Already retained" if result.duplicate else "Retained"
            print(f"\n{state} research note: {result.source.path}")
        return

    if arguments.command == "telegram":
        token = os.environ.get("TELEGRAM_BOT_TOKEN")
        if not token:
            print("Set TELEGRAM_BOT_TOKEN before using `steward telegram`.")
            return
        model_gateway = _model_gateway_from_settings(settings, command="telegram")
        if model_gateway is None:
            return
        database_path = settings.data_dir / "steward.db"
        embedding_provider = SentenceTransformerEmbeddingProvider()
        graph = _build_question_graph(
            settings,
            model_gateway,
            limit=arguments.limit,
            embedding_provider=embedding_provider,
        )
        sources = SourceRepository(database_path)
        activity = ActivityService(database_path)
        fragments = SourceFragmentRepository(database_path)
        source_service = SourceService(sources, fragments, MarkdownExtractor())

        def telegram_runtime_status() -> tuple[str, ...]:
            """Return owner-safe operational labels without creating local state."""
            operational = _database_health(database_path)
            checkpoints = _database_health(settings.data_dir / "checkpoints.db")
            try:
                roots = SourceRootRepository(database_path).list_all() if operational == "available" else ()
                root_summary = (
                    f"{sum(root.health == 'available' for root in roots)} available, "
                    f"{sum(root.health == 'missing' for root in roots)} missing, "
                    f"{sum(root.health == 'disabled' for root in roots)} disabled"
                    if operational == "available" else "unavailable"
                )
            except sqlite3.Error:
                root_summary = "unavailable"
            return (
                f"Operational database: {operational}",
                f"Conversation checkpoints: {checkpoints}",
                f"Authorized roots: {root_summary}",
                f"Model provider: {settings.model_provider} configured",
            )

        def rebuild_semantic_index() -> int:
            """Use the already-local embedding model only after Telegram approval."""
            semantic_service = SourceService(
                sources,
                fragments,
                MarkdownExtractor(),
                semantic_index=SQLiteSemanticIndex(
                    database_path, embedding_provider
                ),
            )
            return semantic_service.rebuild_semantic_index()

        tasks = TaskService(database_path)
        task_reminders = TaskReminderService(database_path, tasks, activity)
        capture_service = InboxCaptureService(
            settings.inbox_dir,
            sources,
            fragments,
            activity,
        )
        deterministic_organization = OrganizationService()
        model_organization = ModelAssistedOrganizationService(model_gateway, fallback=deterministic_organization)
        privacy = PrivacyService(database_path)
        tool_agent_application = None
        tool_model = _tool_calling_model_from_settings(settings)
        if tool_model is not None:
            tool_checkpoint_connection = sqlite3.connect(
                settings.data_dir / "checkpoints.db", check_same_thread=False
            )
            tool_checkpointer = SqliteSaver(tool_checkpoint_connection)
            tool_checkpointer.setup()
            tool_service = ReadOnlyToolService(
                sources,
                fragments,
                LexicalSearchService(sources, fragments),
                KnowledgeService(database_path),
                RecordService(database_path),
                WorkspaceRepository(database_path),
                activity,
                privacy,
                model_is_local=settings.model_provider == "local",
            )
            telegram_tools = build_read_only_tools(tool_service)
            telegram_definitions = list(READ_ONLY_TOOL_DEFINITIONS)
            calendar_token = settings.data_dir / "config" / "google-calendar-token.json"
            if os.environ.get("STEWARD_GOOGLE_CLIENT_SECRETS") and calendar_token.is_file():
                telegram_tools.extend(
                    build_calendar_read_tools(CalendarReadToolService(_calendar_reader_factory(settings)))
                )
                telegram_definitions.extend(CALENDAR_READ_TOOL_DEFINITIONS)
            tool_agent_application = StewardToolAgentApplication(
                build_tool_agent_graph(
                    tool_model,
                    telegram_tools,
                    checkpointer=tool_checkpointer,
                    tool_policy=ToolPolicy(telegram_definitions),
                )
            )

        def propose_captured_source_organization(source, workspaces):
            """Use a permitted model only to make an explicit organization proposal."""

            source_id = source.id or 0
            permitted = (
                privacy.permits_local_model(source_id)
                if settings.model_provider == "local"
                else privacy.permits_external_model(source_id)
            )
            if not permitted:
                return deterministic_organization.propose(source, workspaces)
            return model_organization.propose(
                source,
                workspaces,
                [fragment.text for fragment in fragments.list_for_source(source_id)],
            )
        proposals = OrganizationProposalRepository(database_path)
        review_contexts = ReviewContextRepository(database_path)
        approval = OrganizationApprovalService(
            proposals,
            sources,
            FileMutationService(sources, activity),
            activity,
            WorkspaceRepository(database_path),
        )
        approval_connection = sqlite3.connect(
            settings.data_dir / "checkpoints.db", check_same_thread=False
        )
        approval_checkpointer = SqliteSaver(approval_connection)
        approval_checkpointer.setup()
        organization_approval = StewardOrganizationApprovalApplication(
            proposals,
            WorkspaceRepository(database_path),
            OrganizationApprovalThreadRepository(database_path),
            activity,
            build_organization_approval_graph(
                proposals,
                checkpointer=approval_checkpointer,
                review_proposal=approval.review,
            ),
            proposal_builder=propose_captured_source_organization,
            source_repository=sources,
            inbox_dir=settings.inbox_dir,
            contexts=review_contexts,
        )
        application = StewardEventApplication(
            StewardQuestionApplication(graph),
            StewardCaptureApplication(capture_service),
            organization_approval_application=organization_approval,
            action_proposal_application=StewardActionProposalApplication(
                ActionProposalRepository(database_path),
                ActionProposalService(
                    ActionProposalRepository(database_path),
                    WorkspaceRepository(database_path),
                    activity,
                ),
                CalendarEventProposalService(
                    ActionProposalRepository(database_path),
                    RecordService(database_path),
                    activity,
                    tasks,
                ),
                _calendar_writer_factory(settings, database_path),
                record_service=RecordService(database_path),
                fragment_repository=fragments,
                activity_service=activity,
                task_service=tasks,
                task_reminder_service=task_reminders,
                capture_service=capture_service,
                workspace_repository=WorkspaceRepository(database_path),
                source_repository=sources,
                delivery_repository=TelegramUpdateDeliveryRepository(database_path),
                source_service=source_service,
                semantic_index_rebuilder=rebuild_semantic_index,
            ),
            drive_import_application=StewardDriveImportApplication(
                _drive_inbox_importer(settings, capture_service)
            ),
            gmail_import_application=StewardGmailImportApplication(
                _gmail_inbox_importer(settings, capture_service)
            ),
            read_application=StewardReadApplication(
                sources,
                fragments,
                LexicalSearchService(sources, fragments),
                WorkspaceRepository(database_path),
                activity,
                settings.inbox_dir,
                ActionProposalRepository(database_path),
                TelegramUpdateDeliveryRepository(database_path),
                SemanticSearchService(
                    sources, SQLiteSemanticIndex(database_path, embedding_provider)
                ),
                HybridRetriever(
                    LexicalSearchService(sources, fragments),
                    SemanticSearchService(
                        sources, SQLiteSemanticIndex(database_path, embedding_provider)
                    ),
                ),
                runtime_status=telegram_runtime_status,
                contexts=review_contexts,
            ),
            provisional_intake_application=StewardProvisionalIntakeApplication(
                ProvisionalIntakeService(
                    settings.data_dir / "cache" / "intake",
                    ProvisionalIntakeRepository(database_path),
                    capture_service,
                    activity,
                    privacy,
                ),
                contexts=review_contexts,
            ),
            tool_agent_application=tool_agent_application,
            review_inbox_application=StewardReviewInboxApplication(
                ActionProposalRepository(database_path),
                proposals,
                sources,
                intakes=ProvisionalIntakeRepository(database_path),
                knowledge_proposals=KnowledgeEnrichmentProposalRepository(database_path),
                contexts=review_contexts,
            ),
            record_application=StewardRecordApplication(
                RecordService(database_path), fragments, ActionProposalRepository(database_path), activity
            ),
            task_application=StewardTaskApplication(
                tasks, ActionProposalRepository(database_path), activity, task_reminders
            ),
            research_application=StewardResearchApplication(
                lambda: _research_provider_from_settings(settings, "auto"),
                ResearchRetentionService(capture_service),
            ),
            curated_note_application=StewardCuratedNoteApplication(
                ActionProposalRepository(database_path),
                activity,
                local_model=(
                    OllamaModelGateway(model=settings.local_model, base_url=settings.local_model_url)
                    if settings.local_model else None
                ),
                external_model=(model_gateway if settings.model_provider != "local" else None),
                contexts=review_contexts,
            ),
            workspace_link_application=StewardWorkspaceLinkApplication(
                ActionProposalRepository(database_path), WorkspaceRepository(database_path), sources, activity
            ),
            integration_status_application=StewardIntegrationStatusApplication(settings.data_dir),
            knowledge_application=StewardKnowledgeApplication(
                KnowledgeService(database_path), fragments,
                KnowledgeEnrichmentProposalRepository(database_path), activity,
                KnowledgeConnector(database_path), sources,
            ),
            roots_application=StewardRootsApplication(SourceRootRepository(database_path)),
            privacy_application=StewardPrivacyApplication(
                PrivacyService(database_path),
                sources,
                activity,
                ActionProposalRepository(database_path),
            ),
            operations_application=StewardOperationsApplication(
                TelegramUpdateDeliveryRepository(database_path),
                ActionProposalRepository(database_path),
                activity,
            ),
            calendar_application=StewardCalendarApplication(_calendar_reader_factory(settings)),
        )
        run_telegram_polling(
            token,
            application,
            application,
            allowed_chat_ids=settings.telegram_allowed_chat_ids,
            delivery_repository=TelegramUpdateDeliveryRepository(database_path),
            callback_repository=TelegramCallbackRepository(database_path),
            review_contexts=review_contexts,
            task_reminders=task_reminders,
        )
        return

    if arguments.command == "telegram-deliveries":
        database_path = settings.data_dir / "steward.db"
        initialize_database(database_path)
        try:
            deliveries = TelegramUpdateDeliveryRepository(database_path).list_recent(
                limit=arguments.limit
            )
        except ValueError as error:
            print(str(error))
            return
        if not deliveries:
            print("No local Telegram delivery records.")
            return
        for delivery in deliveries:
            print(
                f"{delivery.update_id}\t{delivery.status}\t{delivery.claimed_at.isoformat()}\t"
                f"{delivery.delivered_at.isoformat() if delivery.delivered_at else ''}"
            )
        return

    if arguments.command == "telegram-delivery-history":
        database_path = settings.data_dir / "steward.db"
        initialize_database(database_path)
        try:
            history = TelegramUpdateDeliveryRepository(database_path).list_history(
                limit=arguments.limit
            )
        except ValueError as error:
            print(str(error))
            return
        if not history:
            print("No local Telegram delivery history.")
            return
        for event in history:
            print(f"{event.update_id}\t{event.event_type}\t{event.occurred_at.isoformat()}")
        return

    if arguments.command == "telegram-dead-letters":
        database_path = settings.data_dir / "steward.db"
        initialize_database(database_path)
        try:
            dead_letters = TelegramUpdateDeliveryRepository(database_path).list_dead_letters(
                limit=arguments.limit
            )
        except ValueError as error:
            print(str(error))
            return
        if not dead_letters:
            print("No terminal Telegram delivery failures.")
            return
        for dead_letter in dead_letters:
            print(f"{dead_letter.update_id}\t{dead_letter.attempts}\t{dead_letter.failed_at.isoformat()}")
        return

    if arguments.command == "telegram-recover-dead-letter":
        if not arguments.confirm:
            print(
                "Refusing to reopen a Telegram dead letter without --confirm. "
                "This only permits a future genuine Telegram redelivery; it cannot replay the original message."
            )
            return
        database_path = settings.data_dir / "steward.db"
        initialize_database(database_path)
        try:
            TelegramUpdateDeliveryRepository(database_path).reopen_dead_letter(arguments.update_id)
        except ValueError as error:
            print(str(error))
            return
        ActivityService(database_path).record(
            ActivityType.TELEGRAM_DELIVERY_RECOVERED,
            object_id=arguments.update_id,
            details="Retry budget reopened for a future genuine Telegram redelivery; no message was replayed.",
        )
        print(
            f"Reopened {arguments.update_id} for a future genuine Telegram redelivery. "
            "No original Telegram message was replayed."
        )
        return

    if arguments.command == "ui":
        database_path = settings.data_dir / "steward.db"
        initialize_database(database_path)
        sources = SourceRepository(database_path)
        fragments = SourceFragmentRepository(database_path)
        lexical = LexicalSearchService(sources, fragments)
        service = (
            HybridRetriever(
                lexical,
                SemanticSearchService(
                    sources, SQLiteSemanticIndex(database_path, SentenceTransformerEmbeddingProvider())
                ),
            )
            if arguments.mode == "hybrid"
            else lexical
        )
        run_local_ui(
            service,
            LocalSourceBrowser(sources, fragments),
            LocalRecordBrowser(RecordService(database_path)),
            host=arguments.host,
            port=arguments.port,
            mode=arguments.mode,
        )
        return

    if arguments.command == "calendar-authorize":
        token_path = arguments.token_file or settings.data_dir / "config" / "google-calendar-token.json"
        authorize_google_calendar(arguments.client_secrets, token_path)
        print(f"Google Calendar read access authorized. Token stored at {token_path}.")
        return

    if arguments.command == "drive-authorize":
        token_path = arguments.token_file or settings.data_dir / "config" / "google-drive-token.json"
        authorize_google_drive(arguments.client_secrets, token_path)
        print(f"Google Drive read access authorized. Token stored at {token_path}.")
        return

    if arguments.command == "drive-search":
        client_secrets = arguments.client_secrets
        if client_secrets is None:
            configured = os.environ.get("STEWARD_GOOGLE_CLIENT_SECRETS")
            if not configured:
                print("Set STEWARD_GOOGLE_CLIENT_SECRETS or pass --client-secrets before reading Drive.")
                return
            client_secrets = Path(configured)
        drive = GoogleDriveService(
            authorize_google_drive(client_secrets, settings.data_dir / "config" / "google-drive-token.json")
        )
        for item in drive.search(arguments.query, limit=arguments.limit):
            print(f"{item.id}\t{item.mime_type}\t{item.name}\t{item.web_view_link or ''}")
        return

    if arguments.command == "drive-import":
        client_secrets = arguments.client_secrets
        if client_secrets is None:
            configured = os.environ.get("STEWARD_GOOGLE_CLIENT_SECRETS")
            if not configured:
                print("Set STEWARD_GOOGLE_CLIENT_SECRETS or pass --client-secrets before importing from Drive.")
                return
            client_secrets = Path(configured)
        database_path = settings.data_dir / "steward.db"
        initialize_database(database_path)
        result = DriveInboxImportService(
            GoogleDriveService(
                authorize_google_drive(client_secrets, settings.data_dir / "config" / "google-drive-token.json")
            ),
            InboxCaptureService(
                settings.inbox_dir,
                SourceRepository(database_path),
                SourceFragmentRepository(database_path),
                ActivityService(database_path),
            ),
        ).import_file(arguments.file_id)
        status = "Already imported" if result.duplicate else "Imported"
        print(f"{status} Drive file to Inbox: {result.source.path}")
        return

    if arguments.command == "gmail-authorize":
        token_path = arguments.token_file or settings.data_dir / "config" / "gmail-token.json"
        authorize_gmail(arguments.client_secrets, token_path)
        print(f"Gmail read access authorized. Token stored at {token_path}.")
        return

    if arguments.command == "gmail-search":
        client_secrets = arguments.client_secrets
        if client_secrets is None:
            configured = os.environ.get("STEWARD_GOOGLE_CLIENT_SECRETS")
            if not configured:
                print("Set STEWARD_GOOGLE_CLIENT_SECRETS or pass --client-secrets before reading Gmail.")
                return
            client_secrets = Path(configured)
        gmail = GmailService(authorize_gmail(client_secrets, settings.data_dir / "config" / "gmail-token.json"))
        for item in gmail.search(arguments.query, limit=arguments.limit):
            print(f"{item.id}\t{item.received_at or ''}\t{item.sender or ''}\t{item.subject}\t{item.snippet}")
        return

    if arguments.command == "gmail-import":
        client_secrets = arguments.client_secrets or (
            Path(os.environ["STEWARD_GOOGLE_CLIENT_SECRETS"])
            if os.environ.get("STEWARD_GOOGLE_CLIENT_SECRETS") else None
        )
        if client_secrets is None:
            print("Set STEWARD_GOOGLE_CLIENT_SECRETS or pass --client-secrets before importing from Gmail.")
            return
        database_path = settings.data_dir / "steward.db"; initialize_database(database_path)
        result = GmailInboxImportService(
            GmailService(authorize_gmail(client_secrets, settings.data_dir / "config" / "gmail-token.json")),
            InboxCaptureService(settings.inbox_dir, SourceRepository(database_path), SourceFragmentRepository(database_path), ActivityService(database_path)),
        ).import_message(arguments.message_id)
        print(("Already imported" if result.duplicate else "Imported") + f" Gmail message to Inbox: {result.source.path}")
        return

    if arguments.command in {"calendar-search", "calendar-get"}:
        client_secrets = arguments.client_secrets
        if client_secrets is None:
            configured = os.environ.get("STEWARD_GOOGLE_CLIENT_SECRETS")
            if not configured:
                print("Set STEWARD_GOOGLE_CLIENT_SECRETS or pass --client-secrets before reading Calendar.")
                return
            client_secrets = Path(configured)
        token_path = settings.data_dir / "config" / "google-calendar-token.json"
        try:
            calendar = CalendarService(authorize_google_calendar(client_secrets, token_path))
            if arguments.command == "calendar-search":
                events = calendar.search(arguments.query, time_min=arguments.after, time_max=arguments.before, limit=arguments.limit)
                for event in events:
                    print(f"{event.id}\t{event.start}\t{event.end}\t{event.summary}")
            else:
                event = calendar.get_event(arguments.event_id)
                print(f"{event.id}\t{event.start}\t{event.end}\t{event.summary}")
        except Exception as error:
            print(f"Calendar is temporarily unavailable: {error}")
        return

    if arguments.command == "calendar-create-travel-event":
        client_secrets = arguments.client_secrets
        if client_secrets is None:
            configured = os.environ.get("STEWARD_GOOGLE_CLIENT_SECRETS")
            if not configured:
                print("Set STEWARD_GOOGLE_CLIENT_SECRETS or pass --client-secrets before writing Calendar.")
                return
            client_secrets = Path(configured)
        database_path = settings.data_dir / "steward.db"
        initialize_database(database_path)
        record = next((item for item in RecordService(database_path).list_travel_records() if item.id == arguments.record_id), None)
        if record is None:
            print(f"Travel record {arguments.record_id} was not found.")
            return
        calendar = CalendarService(
            authorize_google_calendar(
                client_secrets,
                settings.data_dir / "config" / "google-calendar-token.json",
                scopes=(GOOGLE_CALENDAR_EVENTS_SCOPE,),
            )
        )
        event = CalendarWriteService(calendar, database_path, ActivityService(database_path)).create_travel_event(record)
        print(f"Calendar event {event.id} created or already linked.")
        return

    if arguments.command == "calendar-propose-travel-event":
        database_path = settings.data_dir / "steward.db"
        initialize_database(database_path)
        try:
            proposal = CalendarEventProposalService(
                ActionProposalRepository(database_path),
                RecordService(database_path),
                ActivityService(database_path),
            ).propose_travel_event(arguments.record_id)
        except ValueError as error:
            print(str(error))
            return
        print(
            f"Calendar proposal {proposal.id} pending for travel record {arguments.record_id}. "
            f"Review with `steward calendar-review-travel-event {proposal.id} accepted`."
        )
        return

    if arguments.command == "calendar-review-travel-event":
        database_path = settings.data_dir / "steward.db"
        initialize_database(database_path)
        proposals = CalendarEventProposalService(
            ActionProposalRepository(database_path),
            RecordService(database_path),
            ActivityService(database_path),
        )
        writer = None
        if arguments.status == "accepted":
            client_secrets = arguments.client_secrets or (
                Path(os.environ["STEWARD_GOOGLE_CLIENT_SECRETS"])
                if os.environ.get("STEWARD_GOOGLE_CLIENT_SECRETS") else None
            )
            if client_secrets is None:
                print("Set STEWARD_GOOGLE_CLIENT_SECRETS or pass --client-secrets before writing Calendar.")
                return
            writer = CalendarWriteService(
                CalendarService(
                    authorize_google_calendar(
                        client_secrets,
                        settings.data_dir / "config" / "google-calendar-token.json",
                        scopes=(GOOGLE_CALENDAR_EVENTS_SCOPE,),
                    )
                ),
                database_path,
                ActivityService(database_path),
            )
        try:
            proposal = proposals.review(arguments.proposal_id, arguments.status, writer)
        except ValueError as error:
            print(str(error))
            return
        print(f"Calendar proposal {proposal.id} {proposal.status}.")
        return

    if arguments.command in {"create-workspace", "workspaces", "link-source"}:
        database_path = settings.data_dir / "steward.db"
        initialize_database(database_path)
        repository = WorkspaceRepository(database_path)
        service = WorkspaceService(repository, ActivityService(database_path))
        if arguments.command == "create-workspace":
            workspace = service.create(arguments.name)
            print(f"Created workspace {workspace.id}: {workspace.name}")
        elif arguments.command == "link-source":
            service.add_source(arguments.workspace_id, arguments.source_id)
            print(f"Linked source {arguments.source_id} to workspace {arguments.workspace_id}")
        else:
            for workspace in repository.list_all():
                print(f"{workspace.id}\t{workspace.status}\t{workspace.name}")
        return

    if arguments.command in {"action-proposals", "review-action-proposal"}:
        database_path = settings.data_dir / "steward.db"
        initialize_database(database_path)
        repository = ActionProposalRepository(database_path)
        service = ActionProposalService(
            repository,
            WorkspaceRepository(database_path),
            ActivityService(database_path),
        )
        if arguments.command == "review-action-proposal":
            proposal, workspace = service.review(arguments.proposal_id, arguments.status)
            if workspace is None:
                print(f"Action proposal {proposal.id} {proposal.status}.")
            else:
                print(
                    f"Action proposal {proposal.id} accepted: "
                    f"workspace {workspace.id} {workspace.name} is available."
                )
        else:
            for proposal in repository.list_all():
                print(
                    f"{proposal.id}\t{proposal.status}\t{proposal.action_type}\t"
                    f"{proposal.payload}"
                )
        return

    if arguments.command == "review-inbox-workspaces":
        database_path = settings.data_dir / "steward.db"
        initialize_database(database_path)
        proposals = WorkspaceDetectionService().propose(
            SourceRepository(database_path).list_active(),
            WorkspaceRepository(database_path).list_all(),
        )
        if not proposals:
            print("No coherent new workspace candidates found in Inbox.")
            return
        for proposal in proposals:
            print(
                f"{proposal.proposed_name}\tconfidence={proposal.confidence:.2f}\t"
                f"sources={','.join(str(source_id) for source_id in proposal.source_ids)}\t{proposal.rationale}"
            )
        return

    if arguments.command == "connect-knowledge":
        database_path = settings.data_dir / "steward.db"
        initialize_database(database_path)
        proposals = KnowledgeConnector(database_path).propose()
        if not proposals:
            print("No evidence-backed knowledge connections found.")
            return
        for proposal in proposals:
            print(
                f"{proposal.left_concept_name} ↔ {proposal.right_concept_name}\t"
                f"confidence={proposal.confidence:.2f}\t"
                f"fragments={','.join(str(fragment_id) for fragment_id in proposal.supporting_fragment_ids)}\t"
                f"{proposal.rationale}"
            )
        return

    if arguments.command in {
        "propose-knowledge-enrichment",
        "knowledge-enrichment-proposals",
        "review-knowledge-enrichment",
    }:
        database_path = settings.data_dir / "steward.db"
        initialize_database(database_path)
        repository = KnowledgeEnrichmentProposalRepository(database_path)
        activity = ActivityService(database_path)
        if arguments.command == "knowledge-enrichment-proposals":
            proposals = repository.list_all()
            if not proposals:
                print("No knowledge enrichment proposals.")
                return
            for proposal in proposals:
                print(
                    f"{proposal.id}\t{proposal.status}\t{proposal.operation.value}\t"
                    f"claim={proposal.claim_id}\tfragment={proposal.fragment_id}\t{proposal.rationale}"
                )
            return
        if arguments.command == "review-knowledge-enrichment":
            try:
                proposal = repository.review(arguments.proposal_id, arguments.status)
            except ValueError as error:
                print(str(error))
                return
            activity.record(
                ActivityType.KNOWLEDGE_ENRICHMENT_ACCEPTED
                if proposal.status == "accepted"
                else ActivityType.KNOWLEDGE_ENRICHMENT_REJECTED,
                object_id=str(proposal.id),
                details=f"{proposal.operation.value}: {proposal.rationale}",
            )
            print(f"Knowledge enrichment proposal {proposal.id} {proposal.status}.")
            return
        knowledge = KnowledgeService(database_path)
        claim = knowledge.get_claim(arguments.claim_id)
        fragment = SourceFragmentRepository(database_path).get(arguments.fragment_id)
        if claim is None:
            print(f"Claim {arguments.claim_id} was not found.")
            return
        if fragment is None:
            print(f"Fragment {arguments.fragment_id} was not found.")
            return
        if arguments.model_assisted:
            model = _model_gateway_from_settings(settings, command="propose-knowledge-enrichment")
            if model is None:
                return
            privacy = PrivacyService(database_path)
            permitted = (
                privacy.permits_local_model(fragment.source_id)
                if settings.model_provider == "local"
                else privacy.permits_external_model(fragment.source_id)
            )
            if not permitted:
                print("This source's privacy policy does not permit the configured model.")
                return
            proposal = ModelAssistedKnowledgeService(model, fallback=knowledge).compare_evidence(claim, fragment)
        else:
            proposal = knowledge.compare_evidence(
                claim, fragment_id=fragment.id or 0, evidence_text=fragment.text
            )
        stored = repository.add(proposal)
        activity.record(
            ActivityType.KNOWLEDGE_ENRICHMENT_PROPOSED,
            object_id=str(stored.id),
            details=f"{stored.operation.value}: {stored.rationale}",
        )
        print(
            f"Knowledge enrichment proposal {stored.id} pending: {stored.operation.value}\t"
            f"claim={stored.claim_id}\tfragment={stored.fragment_id}\t{stored.rationale}"
        )
        return

    if arguments.command in {"set-source-privacy", "source-privacy"}:
        database_path = settings.data_dir / "steward.db"
        initialize_database(database_path)
        privacy = PrivacyService(database_path)
        if arguments.command == "set-source-privacy":
            privacy.set_rule(arguments.source_id, PrivacyRule(arguments.rule))
            print(f"Source {arguments.source_id} privacy set to {arguments.rule}.")
        else:
            print(privacy.rule_for(arguments.source_id).value)
        return

    if arguments.command in {"propose-organization", "organization-proposals", "review-proposal"}:
        database_path = settings.data_dir / "steward.db"
        initialize_database(database_path)
        proposals = OrganizationProposalRepository(database_path)
        if arguments.command == "propose-organization":
            source = SourceRepository(database_path).get_by_id(arguments.source_id)
            if source is None:
                print(f"Source {arguments.source_id} was not found.")
                return
            workspaces = WorkspaceRepository(database_path).list_all()
            if arguments.model_assisted:
                privacy = PrivacyService(database_path)
                permitted = (
                    privacy.permits_local_model(source.id or 0)
                    if settings.model_provider == "local"
                    else privacy.permits_external_model(source.id or 0)
                )
                if not permitted:
                    print("This source's privacy policy does not permit the configured model.")
                    return
                model = _model_gateway_from_settings(settings, command="propose-organization")
                if model is None:
                    return
                fragments = SourceFragmentRepository(database_path).list_for_source(source.id or 0)
                proposal = ModelAssistedOrganizationService(model).propose(
                    source, workspaces, [fragment.text for fragment in fragments]
                )
            else:
                proposal = OrganizationService().propose(source, workspaces)
            proposal_id = proposals.add(proposal)
            ActivityService(database_path).record(
                ActivityType.ORGANIZATION_PROPOSED,
                object_id=str(proposal_id), details=proposal.rationale,
            )
            print(f"Created proposal {proposal_id}: {proposal.rationale}")
        elif arguments.command == "review-proposal":
            activity = ActivityService(database_path)
            approval = OrganizationApprovalService(
                proposals,
                SourceRepository(database_path),
                FileMutationService(SourceRepository(database_path), activity),
                activity,
                WorkspaceRepository(database_path),
            )
            approval.review(arguments.proposal_id, arguments.status)
            print(f"Proposal {arguments.proposal_id} {arguments.status}.")
        else:
            for proposal in proposals.list_all():
                print(
                    f"{proposal.id}\t{proposal.status}\t{proposal.proposal_type}\t"
                    f"confidence={proposal.confidence:.2f}\tsource={proposal.source_id}\t{proposal.rationale}"
                )
        return

    if arguments.command == "activity":
        database_path = settings.data_dir / "steward.db"
        initialize_database(database_path)
        for event in ActivityService(database_path).list_recent():
            print(f"{event.id}\t{event.event_type.value}\t{event.object_id or ''}\t{event.details}")
        return

    if arguments.command == "propose-travel-record":
        database_path = settings.data_dir / "steward.db"
        initialize_database(database_path)
        fragments = SourceFragmentRepository(database_path).list_for_source(arguments.source_id)
        proposal = RecordService(database_path).propose_travel_record(
            arguments.source_id, [(fragment.id or 0, fragment.text) for fragment in fragments]
        )
        record = proposal.record
        print(f"flight={record.flight_number or ''}\tdeparture={record.departure or ''}\tarrival={record.arrival or ''}\tevidence={proposal.field_evidence}")
        return

    if arguments.command == "create-travel-record":
        database_path = settings.data_dir / "steward.db"
        initialize_database(database_path)
        fragments = SourceFragmentRepository(database_path).list_for_source(arguments.source_id)
        records = RecordService(database_path)
        proposal = records.propose_travel_record(
            arguments.source_id, [(fragment.id or 0, fragment.text) for fragment in fragments]
        )
        record = records.create_from_proposal(proposal)
        print(f"Created travel record {record.id} from source {record.source_id}.")
        return

    if arguments.command == "travel-records":
        database_path = settings.data_dir / "steward.db"
        initialize_database(database_path)
        for record in RecordService(database_path).list_travel_records():
            print(f"{record.id}\t{record.flight_number or ''}\t{record.departure or ''}\t{record.arrival or ''}")
        return

    if arguments.command == "propose-receipt-record":
        database_path = settings.data_dir / "steward.db"
        initialize_database(database_path)
        fragments = SourceFragmentRepository(database_path).list_for_source(arguments.source_id)
        proposal = RecordService(database_path).propose_receipt_record(
            arguments.source_id, [(fragment.id or 0, fragment.text) for fragment in fragments]
        )
        record = proposal.record
        amount = f"{record.total_cents / 100:.2f}" if record.total_cents is not None else ""
        print(f"merchant={record.merchant or ''}\ttotal={amount}\tcurrency={record.currency or ''}\tevidence={proposal.field_evidence}")
        return

    if arguments.command == "create-receipt-record":
        database_path = settings.data_dir / "steward.db"
        initialize_database(database_path)
        fragments = SourceFragmentRepository(database_path).list_for_source(arguments.source_id)
        records = RecordService(database_path)
        proposal = records.propose_receipt_record(
            arguments.source_id, [(fragment.id or 0, fragment.text) for fragment in fragments]
        )
        record = records.create_receipt_from_proposal(proposal)
        print(f"Created receipt record {record.id} from source {record.source_id}.")
        return

    if arguments.command == "receipt-records":
        database_path = settings.data_dir / "steward.db"
        initialize_database(database_path)
        for record in RecordService(database_path).list_receipt_records():
            total = f"{record.total_cents / 100:.2f}" if record.total_cents is not None else ""
            print(f"{record.id}\t{record.merchant or ''}\t{total}\t{record.currency or ''}\t{record.receipt_number or ''}")
        return

    if arguments.command == "propose-warranty-record":
        database_path = settings.data_dir / "steward.db"
        initialize_database(database_path)
        fragments = SourceFragmentRepository(database_path).list_for_source(arguments.source_id)
        proposal = RecordService(database_path).propose_warranty_record(
            arguments.source_id, [(fragment.id or 0, fragment.text) for fragment in fragments]
        )
        record = proposal.record
        print(f"product={record.product_name or ''}\tprovider={record.provider or ''}\twarranty={record.warranty_number or ''}\tevidence={proposal.field_evidence}")
        return

    if arguments.command == "create-warranty-record":
        database_path = settings.data_dir / "steward.db"
        initialize_database(database_path)
        fragments = SourceFragmentRepository(database_path).list_for_source(arguments.source_id)
        records = RecordService(database_path)
        proposal = records.propose_warranty_record(
            arguments.source_id, [(fragment.id or 0, fragment.text) for fragment in fragments]
        )
        record = records.create_warranty_from_proposal(proposal)
        print(f"Created warranty record {record.id} from source {record.source_id}.")
        return

    if arguments.command == "warranty-records":
        database_path = settings.data_dir / "steward.db"
        initialize_database(database_path)
        for record in RecordService(database_path).list_warranty_records():
            print(f"{record.id}\t{record.product_name or ''}\t{record.provider or ''}\t{record.warranty_number or ''}")
        return

    if arguments.command in {"travel-record-references", "add-travel-record-reference"}:
        database_path = settings.data_dir / "steward.db"
        initialize_database(database_path)
        records = RecordService(database_path)
        if arguments.command == "add-travel-record-reference":
            reference = records.add_reference(
                arguments.record_id, arguments.reference_type, arguments.value, arguments.fragment_id
            )
            print(f"Added reference {reference.id}: {reference.reference_type}={reference.value}")
        else:
            for reference in records.list_references(arguments.record_id):
                print(f"{reference.id}\t{reference.reference_type}\t{reference.value}\tfragment={reference.fragment_id}")
        return

    logging.getLogger(__name__).info("Steward foundation started")
    build_parser().print_help()


if __name__ == "__main__":
    main()
