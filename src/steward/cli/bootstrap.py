"""Compose Steward services from settings for the CLI and the Telegram runtime."""

from __future__ import annotations

import os
import sqlite3
from contextlib import closing
from pathlib import Path

from steward.config import Settings
from steward.app import (
    StewardCaptureApplication,
    StewardDriveImportApplication,
    StewardGmailImportApplication,
    StewardEventApplication,
    StewardQuestionApplication,
    StewardReadApplication,
    StewardProvisionalIntakeApplication,
    StewardToolAgentApplication,
    StewardRootsApplication,
    StewardMoveReconciliationApplication,
    StewardCodexHandoffApplication,
    StewardPrivacyApplication,
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
from steward.extraction import SourceFragmentRepository
from steward.graphs import build_retrieval_answer_graph
from steward.graphs import (
    GeminiToolCallingModel,
    OllamaToolCallingModel,
    OpenAICompatibleToolCallingModel,
    build_tool_agent_graph,
)
from steward.sources import CodexHandoffService, SourceMoveProposalRepository, SourceRepository
from steward.sources.export import SourceExportService
from steward.storage import initialize_database
from steward.retrieval import (
    HybridRetriever,
    LexicalSearchService,
    SemanticSearchService,
    SentenceTransformerEmbeddingProvider,
    SQLiteSemanticIndex,
)
from steward.telegram import TelegramUpdateDeliveryRepository
from steward.activity import ActivityService
from steward.action_proposals import ActionProposalRepository
from steward.intake import ProvisionalIntakeRepository, ProvisionalIntakeService
from steward.sources.inbox_context import SourceInboxContextRepository
from steward.roots import SourceRootProfileRepository, SourceRootRepository
from steward.drive import DriveInboxImportService, GoogleDriveService, authorize_google_drive
from steward.gmail import GmailInboxImportService, GmailService, authorize_gmail
from steward.privacy import PrivacyService
from steward.tools import (
    SourceReadOnlyToolService,
    ToolPolicy,
    build_source_read_only_tools,
)
from steward.tools.read_only import SOURCE_READ_ONLY_TOOL_DEFINITIONS
from langgraph.checkpoint.sqlite import SqliteSaver


def model_gateway_from_settings(
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


def tool_calling_model_from_settings(settings: Settings):
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


class ConfiguredDriveInboxImporter:
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

    def search_page(self, query: str, *, page_token: str | None = None):
        configured = os.environ.get("STEWARD_GOOGLE_CLIENT_SECRETS")
        if not configured:
            raise ValueError("Set STEWARD_GOOGLE_CLIENT_SECRETS before searching Drive.")
        return GoogleDriveService(
            authorize_google_drive(Path(configured), self._settings.data_dir / "config" / "google-drive-token.json")
        ).search_page(query, page_token=page_token)


def drive_inbox_importer(
    settings: Settings, capture_service: InboxCaptureService
) -> ConfiguredDriveInboxImporter | None:
    """Expose Drive relay only when this local process has an OAuth client configured."""

    if not os.environ.get("STEWARD_GOOGLE_CLIENT_SECRETS"):
        return None
    return ConfiguredDriveInboxImporter(settings, capture_service)


class ConfiguredGmailInboxImporter:
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

    def search_page(self, query: str, *, page_token: str | None = None):
        configured = os.environ.get("STEWARD_GOOGLE_CLIENT_SECRETS")
        if not configured:
            raise ValueError("Set STEWARD_GOOGLE_CLIENT_SECRETS before searching Gmail.")
        return GmailService(
            authorize_gmail(Path(configured), self._settings.data_dir / "config" / "gmail-token.json")
        ).search_page(query, page_token=page_token)


def gmail_inbox_importer(
    settings: Settings, capture_service: InboxCaptureService
) -> ConfiguredGmailInboxImporter | None:
    if not os.environ.get("STEWARD_GOOGLE_CLIENT_SECRETS"):
        return None
    return ConfiguredGmailInboxImporter(settings, capture_service)


def build_question_graph(
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


def database_health(database_path: Path, *, required_tables: tuple[str, ...] = ()) -> str:
    """Read a database without creating it, because health checks must be non-mutating."""
    if not database_path.is_file():
        return "not initialized"
    try:
        with closing(sqlite3.connect(f"{database_path.resolve().as_uri()}?mode=ro", uri=True)) as connection:
            tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
            if not tables or not set(required_tables).issubset(tables):
                return "not initialized"
    except sqlite3.Error as error:
        message = str(error).casefold()
        return "busy" if "locked" in message or "busy" in message else "unavailable"
    return "available"


def health_report(settings: Settings) -> tuple[str, bool]:
    """Return safe status text and local readiness; never test remote credentials."""
    database_path = settings.data_dir / "steward.db"
    checkpoint_path = settings.data_dir / "checkpoints.db"
    database_status = database_health(database_path, required_tables=("sources", "schema_migrations"))
    checkpoint_health = database_health(checkpoint_path, required_tables=("checkpoints", "writes"))
    root_summary = "not initialized"
    roots_ready = False
    if database_status == "available":
        try:
            roots = SourceRootRepository(database_path).list_all()
        except sqlite3.Error:
            root_summary = "unavailable"
        else:
            available = sum(root.health == "available" for root in roots)
            missing = sum(root.health == "missing" for root in roots)
            disabled = sum(root.health == "disabled" for root in roots)
            root_summary = f"{available} available, {missing} missing, {disabled} disabled"
            roots_ready = missing == 0
    # Keep this aligned with the variable read by the ``telegram`` command.
    telegram = "configured" if os.environ.get("TELEGRAM_BOT_TOKEN", "").strip() else "not configured"
    report = (
        "Steward health:\n"
        f"Operational database: {database_status}\n"
        f"Conversation checkpoints: {checkpoint_health}\n"
        f"Authorized roots: {root_summary}\n"
        f"Telegram token: {telegram}"
    )
    ready = database_status == checkpoint_health == "available" and roots_ready and telegram == "configured"
    return report, ready


def build_source_agent_graph(settings: Settings, tool_calling_model: object, *, activity: ActivityService | None = None):
    """Compose the allowlisted read-only tool loop shared by the CLI and Telegram."""

    database_path = settings.data_dir / "steward.db"
    initialize_database(database_path)
    sources = SourceRepository(database_path)
    fragments = SourceFragmentRepository(database_path)
    checkpoint_connection = sqlite3.connect(
        settings.data_dir / "checkpoints.db", check_same_thread=False
    )
    checkpointer = SqliteSaver(checkpoint_connection)
    checkpointer.setup()
    return build_tool_agent_graph(
        tool_calling_model,
        build_source_read_only_tools(
            SourceReadOnlyToolService(
                sources,
                fragments,
                LexicalSearchService(sources, fragments),
                activity or ActivityService(database_path),
                PrivacyService(database_path),
                model_is_local=settings.model_provider == "local",
            )
        ),
        checkpointer=checkpointer,
        tool_policy=ToolPolicy(SOURCE_READ_ONLY_TOOL_DEFINITIONS),
    )


def _telegram_runtime_status(settings: Settings) -> tuple[str, ...]:
    """Return owner-safe operational labels without creating local state."""
    database_path = settings.data_dir / "steward.db"
    operational = database_health(database_path)
    checkpoints = database_health(settings.data_dir / "checkpoints.db")
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


def build_telegram_application(
    settings: Settings, model_gateway: ModelGateway, *, limit: int
) -> StewardEventApplication:
    """Compose every live Telegram use case over one local database."""

    database_path = settings.data_dir / "steward.db"
    embedding_provider = SentenceTransformerEmbeddingProvider()
    graph = build_question_graph(
        settings, model_gateway, limit=limit, embedding_provider=embedding_provider,
    )
    sources = SourceRepository(database_path)
    activity = ActivityService(database_path)
    fragments = SourceFragmentRepository(database_path)
    roots = SourceRootRepository(database_path)
    inbox_contexts = SourceInboxContextRepository(database_path)
    profiles = SourceRootProfileRepository(database_path)
    lexical = LexicalSearchService(sources, fragments)
    semantic = SemanticSearchService(sources, SQLiteSemanticIndex(database_path, embedding_provider))
    capture_service = InboxCaptureService(settings.inbox_dir, sources, fragments, activity)
    privacy = PrivacyService(database_path)
    review_contexts = ReviewContextRepository(database_path)
    tool_model = tool_calling_model_from_settings(settings)
    tool_agent_application = (
        StewardToolAgentApplication(build_source_agent_graph(settings, tool_model, activity=activity))
        if tool_model is not None else None
    )
    return StewardEventApplication(
        StewardQuestionApplication(graph),
        StewardCaptureApplication(capture_service),
        drive_import_application=StewardDriveImportApplication(
            drive_inbox_importer(settings, capture_service), contexts=review_contexts
        ),
        gmail_import_application=StewardGmailImportApplication(
            gmail_inbox_importer(settings, capture_service), contexts=review_contexts
        ),
        read_application=StewardReadApplication(
            sources,
            fragments,
            lexical,
            activity,
            settings.inbox_dir,
            deliveries=TelegramUpdateDeliveryRepository(database_path),
            semantic_search=semantic,
            hybrid_retriever=HybridRetriever(lexical, semantic),
            runtime_status=lambda: _telegram_runtime_status(settings),
            contexts=review_contexts,
            source_model=model_gateway,
            source_export=SourceExportService(sources, roots, settings.inbox_dir),
            source_model_allowed=(
                privacy.permits_local_model
                if settings.model_provider == "local"
                else privacy.permits_external_model
            ),
            inbox_contexts=inbox_contexts,
            roots=roots,
        ),
        provisional_intake_application=StewardProvisionalIntakeApplication(
            ProvisionalIntakeService(
                settings.data_dir / "cache" / "intake",
                ProvisionalIntakeRepository(database_path),
                capture_service,
                activity,
                privacy,
                roots=roots,
                inbox_contexts=inbox_contexts,
            ),
            contexts=review_contexts,
            roots=roots,
        ),
        tool_agent_application=tool_agent_application,
        roots_application=StewardRootsApplication(roots, contexts=review_contexts, profiles=profiles),
        move_reconciliation_application=StewardMoveReconciliationApplication(
            sources, SourceMoveProposalRepository(database_path)
        ),
        codex_handoff_application=StewardCodexHandoffApplication(
            CodexHandoffService(
                sources, roots, settings.data_dir, settings.inbox_dir, inbox_contexts, profiles,
            ),
            sources,
            settings.inbox_dir,
        ),
        privacy_application=StewardPrivacyApplication(
            privacy, sources, activity, ActionProposalRepository(database_path), contexts=review_contexts,
        ),
    )
