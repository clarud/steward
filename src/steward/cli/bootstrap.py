"""Compose Steward services from settings for the CLI and the Telegram runtime."""

from __future__ import annotations

import logging
import os
import sqlite3
from collections.abc import Callable
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

from steward.activity import ActivityService
from steward.answer import GeminiModelGateway, ModelGateway, OllamaModelGateway, OpenAIModelGateway
from steward.app import (
    StewardAnswersApplication,
    StewardEventApplication,
    StewardFilesApplication,
    StewardIntakeApplication,
)
from steward.capture import InboxCaptureService
from steward.config import Settings
from steward.extras import MissingExtraError
from steward.extraction import MarkdownExtractor, SourceFragmentRepository
from steward.graphs.ask import AskTools, build_ask_graph
from steward.graphs.find import FindTools, build_find_graph
from steward.graphs.summarize import SummarizeTools, build_summarize_graph
from steward.intake import ProvisionalIntakeRepository, ProvisionalIntakeService
from steward.retrieval import (
    HybridRetriever,
    LexicalSearchService,
    SemanticSearchService,
    SentenceTransformerEmbeddingProvider,
    SQLiteSemanticIndex,
)
from steward.reviews import ReviewContextRepository
from steward.roots import SourceRoot, SourceRootRepository
from steward.sources import InboxQueue, MoveReconciler, ReconciledMove, Source, SourceRepository
from steward.sources.export import SourceExportService
from steward.sources.inbox_context import SourceInboxContextRepository
from steward.sources.scanning import ScanResult
from steward.sources.service import SourceService
from steward.sources.summaries import SummaryRepository
from steward.storage import initialize_database

_LOGGER = logging.getLogger(__name__)


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


def model_label(settings: Settings) -> str:
    """Identifies the model in cached summaries, so switching models recomputes them."""
    name = {
        "local": settings.local_model, "gemini": settings.gemini_model,
        "soclaas": settings.soclaas_model, "openai": settings.openai_model,
    }.get(settings.model_provider)
    return f"{settings.model_provider}:{name or 'default'}"


@dataclass
class Flows:
    find: object
    ask: object
    summarize: object


def build_flows(
    settings: Settings,
    model_gateway: ModelGateway | None,
    location: Callable[[Source], str],
    *,
    embedding_provider: SentenceTransformerEmbeddingProvider | None = None,
) -> Flows:
    """Compile Find, Ask, and Summarize over the local database."""
    database_path = settings.data_dir / "steward.db"
    initialize_database(database_path)
    sources = SourceRepository(database_path)
    fragments = SourceFragmentRepository(database_path)
    roots = SourceRootRepository(database_path)
    lexical = LexicalSearchService(sources, fragments)
    semantic = (
        SemanticSearchService(sources, SQLiteSemanticIndex(database_path, embedding_provider))
        if embedding_provider is not None else None
    )
    return Flows(
        find=build_find_graph(FindTools(sources, roots, lexical, semantic, model_gateway, location)),
        ask=build_ask_graph(AskTools(
            sources, fragments, roots, HybridRetriever(lexical, semantic) if semantic else lexical, model_gateway,
        )),
        summarize=build_summarize_graph(SummarizeTools(
            sources, fragments, SummaryRepository(database_path), model_gateway, model_label(settings),
        )),
    )


def optional_embedding_provider() -> SentenceTransformerEmbeddingProvider | None:
    """The local meaning-search model, or None if it isn't installed or downloaded."""
    try:
        return SentenceTransformerEmbeddingProvider()
    except (MissingExtraError, OSError, RuntimeError, ValueError):
        _LOGGER.warning("Meaning search is off: the local embedding model isn't available.")
        return None


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
    database_status = database_health(database_path, required_tables=("sources", "schema_migrations"))
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
        f"Authorized roots: {root_summary}\n"
        f"Telegram token: {telegram}"
    )
    ready = database_status == "available" and roots_ready and telegram == "configured"
    return report, ready




def scan_root(
    settings: Settings, root: SourceRoot, *, embedding_provider: SentenceTransformerEmbeddingProvider | None = None,
) -> tuple[ScanResult, tuple[ReconciledMove, ...]]:
    """Scan one folder, recognise moved files, and refresh INBOX.md."""
    database_path = settings.data_dir / "steward.db"
    sources = SourceRepository(database_path)
    service = SourceService(
        source_repository=sources,
        fragment_repository=SourceFragmentRepository(database_path),
        markdown_extractor=MarkdownExtractor(),
        semantic_index=SQLiteSemanticIndex(database_path, embedding_provider) if embedding_provider else None,
    )
    result = service.scan_source_root(root.path, exclusions=root.exclusions)
    SourceRootRepository(database_path).record_successful_scan(root, result)
    moves = MoveReconciler(sources, settings.inbox_dir).reconcile()
    inbox_queue(settings).refresh()
    return result, moves


def rescan_all(
    settings: Settings, *, embedding_provider: SentenceTransformerEmbeddingProvider | None = None,
) -> tuple[ReconciledMove, ...]:
    """The bot's periodic pass: every available folder, then vectors for anything new."""
    database_path = settings.data_dir / "steward.db"
    moves: list[ReconciledMove] = []
    for root in SourceRootRepository(database_path).list_all():
        if root.health != "available":
            continue
        try:
            moves.extend(scan_root(settings, root, embedding_provider=embedding_provider)[1])
        except (sqlite3.Error, OSError) as error:
            _LOGGER.warning("Background scan of %s failed (%s); will retry.", root.name, type(error).__name__)
    moves.extend(MoveReconciler(SourceRepository(database_path), settings.inbox_dir).reconcile())
    inbox_queue(settings).refresh()
    if embedding_provider is not None:
        SourceService(
            source_repository=SourceRepository(database_path),
            fragment_repository=SourceFragmentRepository(database_path),
            markdown_extractor=MarkdownExtractor(),
            semantic_index=SQLiteSemanticIndex(database_path, embedding_provider),
        ).index_missing_vectors()
    return tuple(moves)


def filed_notices(settings: Settings, moves: tuple[ReconciledMove, ...]) -> list[tuple[str, str]]:
    """Tell the chat that uploaded a file where it was filed, e.g. by Codex."""
    database_path = settings.data_dir / "steward.db"
    inbox = settings.inbox_dir.resolve()
    roots = SourceRootRepository(database_path).list_all()
    notices = []
    for move in moves:
        if move.previous_path.resolve().parent != inbox:
            continue
        with sqlite3.connect(database_path) as connection:
            row = connection.execute(
                "SELECT capture_key FROM inbox_captures WHERE source_id = ?", (move.source.id,)
            ).fetchone()
        platform, _, rest = (str(row[0]) if row else "").partition(":")
        if platform != "telegram":
            continue
        chat_id = rest.partition(":")[0]
        path = move.source.path.resolve()
        root = max(
            (item for item in roots if path.is_relative_to(item.path.resolve())),
            key=lambda item: len(item.path.parts), default=None,
        )
        where = (
            " / ".join((root.name, *path.relative_to(root.path.resolve()).parent.parts))
            if root is not None else str(path.parent.name)
        )
        notices.append((chat_id, f"📁 {path.name} is now filed in {where}."))
    return notices


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
    settings: Settings,
    model_gateway: ModelGateway | None,
    *,
    embedding_provider: SentenceTransformerEmbeddingProvider | None = None,
) -> StewardEventApplication:
    """Compose every Telegram use case over one local database."""

    database_path = settings.data_dir / "steward.db"
    sources = SourceRepository(database_path)
    activity = ActivityService(database_path)
    fragments = SourceFragmentRepository(database_path)
    roots = SourceRootRepository(database_path)
    inbox_contexts = SourceInboxContextRepository(database_path)
    contexts = ReviewContextRepository(database_path)
    capture = InboxCaptureService(settings.inbox_dir, sources, fragments, activity, inbox_queue(settings))
    flows = build_flows(settings, model_gateway, lambda source: files.location(source), embedding_provider=embedding_provider)
    answers = (
        StewardAnswersApplication(
            find_graph=flows.find, ask_graph=flows.ask, summarize_graph=flows.summarize,
            location=lambda source: files.location(source),
        )
        if model_gateway is not None else None
    )
    files = StewardFilesApplication(
        sources, fragments, roots, settings.inbox_dir,
        contexts=contexts,
        inbox_contexts=inbox_contexts,
        source_export=SourceExportService(sources, roots, settings.inbox_dir),
        answers=answers,
    )
    return StewardEventApplication(
        files=files,
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
        answers=answers,
        roots=roots,
    )
