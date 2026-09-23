"""Compose Steward services from settings for the CLI and the Telegram runtime."""

from __future__ import annotations

import os
import sqlite3
from contextlib import closing
from pathlib import Path

from langgraph.checkpoint.sqlite import SqliteSaver

from steward.activity import ActivityService
from steward.answer import (
    AnswerService,
    ContextBuilder,
    GeminiModelGateway,
    ModelGateway,
    OllamaModelGateway,
    OpenAIModelGateway,
)
from steward.app import (
    StewardEventApplication,
    StewardFilesApplication,
    StewardIntakeApplication,
    StewardQuestionApplication,
    StewardSearchApplication,
)
from steward.capture import InboxCaptureService
from steward.config import Settings
from steward.extraction import SourceFragmentRepository
from steward.graphs import build_retrieval_answer_graph
from steward.intake import ProvisionalIntakeRepository, ProvisionalIntakeService
from steward.retrieval import (
    HybridRetriever,
    LexicalSearchService,
    SemanticSearchService,
    SentenceTransformerEmbeddingProvider,
    SQLiteSemanticIndex,
)
from steward.reviews import ReviewContextRepository
from steward.roots import SourceRootRepository
from steward.sources import InboxQueue, SourceRepository
from steward.sources.export import SourceExportService
from steward.sources.inbox_context import SourceInboxContextRepository
from steward.storage import initialize_database


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


def build_question_graph(
    settings: Settings,
    model_gateway: ModelGateway,
    *,
    limit: int,
    embedding_provider: SentenceTransformerEmbeddingProvider | None = None,
):
    """Compose the grounded retrieval-and-answer workflow."""

    database_path = settings.data_dir / "steward.db"
    initialize_database(database_path)
    sources = SourceRepository(database_path)
    fragments = SourceFragmentRepository(database_path)
    retriever = HybridRetriever(
        LexicalSearchService(sources, fragments),
        SemanticSearchService(
            sources, SQLiteSemanticIndex(database_path, embedding_provider or SentenceTransformerEmbeddingProvider()),
        ),
    )
    answer_service = AnswerService(retriever=retriever, context_builder=ContextBuilder(), model_gateway=model_gateway)
    checkpointer = SqliteSaver(sqlite3.connect(settings.data_dir / "checkpoints.db", check_same_thread=False))
    checkpointer.setup()
    return build_retrieval_answer_graph(retriever, answer_service, retrieval_limit=limit, checkpointer=checkpointer)


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
            root_summary = f"{available} available, {missing} missing"
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


def inbox_queue(settings: Settings) -> InboxQueue:
    """The INBOX.md list of Inbox files waiting to be filed on this computer."""
    database_path = settings.data_dir / "steward.db"
    return InboxQueue(
        SourceRepository(database_path),
        settings.inbox_dir,
        roots=SourceRootRepository(database_path),
        inbox_contexts=SourceInboxContextRepository(database_path),
    )


def build_telegram_application(
    settings: Settings, model_gateway: ModelGateway, *, limit: int
) -> StewardEventApplication:
    """Compose every Telegram use case over one local database."""

    database_path = settings.data_dir / "steward.db"
    embedding_provider = SentenceTransformerEmbeddingProvider()
    sources = SourceRepository(database_path)
    activity = ActivityService(database_path)
    fragments = SourceFragmentRepository(database_path)
    roots = SourceRootRepository(database_path)
    inbox_contexts = SourceInboxContextRepository(database_path)
    contexts = ReviewContextRepository(database_path)
    lexical = LexicalSearchService(sources, fragments)
    semantic = SemanticSearchService(sources, SQLiteSemanticIndex(database_path, embedding_provider))
    capture = InboxCaptureService(settings.inbox_dir, sources, fragments, activity, inbox_queue(settings))
    files = StewardFilesApplication(
        sources, fragments, roots, settings.inbox_dir,
        contexts=contexts,
        inbox_contexts=inbox_contexts,
        source_export=SourceExportService(sources, roots, settings.inbox_dir),
        source_model=model_gateway,
    )
    return StewardEventApplication(
        files=files,
        search=StewardSearchApplication(
            sources, lexical, roots, hybrid=HybridRetriever(lexical, semantic), location=files.location,
        ),
        intake=StewardIntakeApplication(
            ProvisionalIntakeService(
                settings.data_dir / "cache" / "intake",
                ProvisionalIntakeRepository(database_path),
                capture,
                activity,
                roots=roots,
                inbox_contexts=inbox_contexts,
            ),
            contexts=contexts,
            roots=roots,
        ),
        question=StewardQuestionApplication(
            build_question_graph(settings, model_gateway, limit=limit, embedding_provider=embedding_provider)
        ),
    )
