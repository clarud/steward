"""Run one `steward` command."""

from __future__ import annotations

import argparse
import os
import sys
import sqlite3
from collections.abc import Sequence
from datetime import datetime

from steward.config import Settings, load_environment_file
from steward.extras import MissingExtraError
from steward.runtime import RuntimeAlreadyRunningError, telegram_runtime_lock
from steward.reviews import MessageReferenceRepository, ReviewContextRepository
from steward.extraction import DocumentExtractionError, MarkdownExtractor, SourceFragmentRepository
from steward.logging import configure_logging
from steward.sources import (
    SourceMoveProposalRepository,
    SourceMoveReconciliationService,
    SourceRepository,
    SourceType,
)
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
from steward.activity import ActivityService, ActivityType
from steward.roots import SourceRootRepository
from steward.evaluation import evaluate_lexical_retrieval, load_retrieval_cases
from steward.cli.bootstrap import (
    build_question_graph,
    build_telegram_application,
    health_report,
    inbox_queue,
    model_gateway_from_settings,
)
from steward.cli.parser import build_parser


def _configure_console_encoding() -> None:
    """Allow source text such as λ to print in legacy Windows terminals."""

    if (
        hasattr(sys.stdout, "reconfigure")
        and (sys.stdout.encoding or "").casefold().replace("-", "") != "utf8"
    ):
        sys.stdout.reconfigure(encoding="utf-8")


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


def main(argv: Sequence[str] | None = None) -> None:
    """Run a Steward command."""
    _configure_console_encoding()
    load_environment_file()
    settings = Settings.from_environment()
    arguments = build_parser().parse_args(argv)
    configure_logging(settings)
    try:
        _run(arguments, settings)
    except MissingExtraError as error:
        print(error)
        raise SystemExit(1) from error


def _run(arguments: argparse.Namespace, settings: Settings) -> None:

    if arguments.command == "health":
        report, ready = health_report(settings)
        print(report)
        if arguments.strict and not ready:
            raise SystemExit(1)
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
        snapshots = []
        reserved = False
        try:
            destination.mkdir(parents=True, exist_ok=False)
            reserved = True
            for path in available:
                snapshots.append(snapshot_database(path, destination / path.name))
        except (OSError, ValueError, sqlite3.Error) as error:
            print(f"Backup failed: {error}")
            if reserved:
                print("Backup set is incomplete. Do not treat this directory as a complete recovery point.")
                if snapshots:
                    print("Completed snapshots retained:\n" + "\n".join(str(path) for path in snapshots))
                print("Retry with a new destination after resolving the failure.")
            return
        print("Backed up local Steward databases:\n" + "\n".join(str(path) for path in snapshots))
        if len(snapshots) > 1:
            print("Databases were copied sequentially. For a coordinated recovery point, stop Steward and other writers before backing up.")
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
            SourceRootRepository(database_path).record_successful_scan(root, result)
            SourceMoveReconciliationService(
                SourceRepository(database_path), SourceMoveProposalRepository(database_path)
            ).propose_for_root(root.path)
            inbox_queue(settings).refresh()
        except sqlite3.Error as error:
            _print_scan_database_error("Root scan", error)
            return
        print(
            f"Scan complete for {root.name}: "
            f"new={result.new} updated={result.updated} "
            f"unchanged={result.unchanged} missing={result.missing}"
        )
        return

    if arguments.command == "onboard-root":
        database_path = settings.data_dir / "steward.db"
        try:
            initialize_database(database_path)
            roots = SourceRootRepository(database_path)
            existing = roots.get_by_name(arguments.name)
            requested_path = arguments.path.resolve()
            if existing is not None:
                if existing.path != requested_path:
                    print(f"A source root named {existing.name!r} already exists at a different path. No state was changed.")
                    return
                if tuple(arguments.exclude) and existing.exclusions != tuple(arguments.exclude):
                    print(f"Source root {existing.name!r} already exists with different exclusions. Use its existing configuration or create a differently named root.")
                    return
                root = existing
                onboarding = "Existing authorization reused"
            else:
                root = roots.add(arguments.name, requested_path, exclusions=tuple(arguments.exclude))
                onboarding = "Authorized"
            if not root.enabled:
                print(f"Source root {root.name!r} is disabled. Enable it locally before scanning.")
                return
            if not root.path.is_dir():
                print(f"Source root {root.name!r} is unavailable: {root.path}")
                return
            result = SourceService(
                source_repository=SourceRepository(database_path),
                fragment_repository=SourceFragmentRepository(database_path),
                markdown_extractor=MarkdownExtractor(),
            ).scan_source_root(root.path, exclusions=root.exclusions)
            SourceRootRepository(database_path).record_successful_scan(root, result)
            SourceMoveReconciliationService(
                SourceRepository(database_path), SourceMoveProposalRepository(database_path)
            ).propose_for_root(root.path)
        except (sqlite3.Error, ValueError) as error:
            if isinstance(error, sqlite3.Error):
                _print_scan_database_error("Onboarding scan", error)
            else:
                print(f"Root onboarding was not completed: {error}")
            return
        print(
            f"{onboarding} source root {root.name!r} and scanned it in place: "
            f"new={result.new} updated={result.updated} unchanged={result.unchanged} missing={result.missing}\n"
            "Original files were not moved, copied, or rewritten."
        )
        return

    if arguments.command == "reconcile-moves":
        database_path = settings.data_dir / "steward.db"; initialize_database(database_path)
        root = SourceRootRepository(database_path).get_by_name(arguments.name)
        if root is None:
            print(f"No locally authorized source root named {arguments.name!r}.")
            return
        proposals = SourceMoveReconciliationService(
            SourceRepository(database_path), SourceMoveProposalRepository(database_path)
        ).propose_for_root(root.path)
        pending = SourceMoveProposalRepository(database_path).list_pending()
        if not pending:
            print("No unambiguous source moves are waiting for review.")
            return
        sources = SourceRepository(database_path)
        for proposal in pending:
            missing = sources.get_by_id(proposal.missing_source_id)
            discovered = sources.get_by_id(proposal.discovered_source_id)
            if missing is not None and discovered is not None:
                print(f"{proposal.id}\tmissing={proposal.missing_source_id}:{missing.path.name}\tfound={proposal.discovered_source_id}:{discovered.path.name}")
        return

    if arguments.command == "review-move":
        database_path = settings.data_dir / "steward.db"; initialize_database(database_path)
        proposals = SourceMoveProposalRepository(database_path)
        service = SourceMoveReconciliationService(SourceRepository(database_path), proposals)
        try:
            if arguments.accept:
                source = service.accept(arguments.proposal_id)
                print(f"Preserved source ID {source.id} at {source.path}. The temporary discovered row was removed.")
            else:
                proposal = proposals.review(arguments.proposal_id, "rejected")
                print(f"Rejected move proposal {proposal.id}; both source histories remain unchanged.")
        except ValueError as error:
            print(f"Source move was not changed: {error}")
        return

    if arguments.command == "inbox":
        database_path = settings.data_dir / "steward.db"; initialize_database(database_path)
        queue = inbox_queue(settings)
        path = queue.refresh()
        entries = queue.pending()
        print(f"{len(entries)} file(s) waiting in the Inbox. List: {path}")
        for entry in entries:
            root = entry.intended_root.name if entry.intended_root is not None else "no intended root"
            print(f"- {entry.source.path.name} ({entry.received_via}; {root})")
        return

    if arguments.command == "relocate-root":
        if not arguments.confirm:
            print("Refusing to relocate a source root without --confirm. No state was changed.")
            return
        database_path = settings.data_dir / "steward.db"
        initialize_database(database_path)
        try:
            relocation = SourceRootRepository(database_path).relocate_missing(arguments.name, arguments.path)
        except (ValueError, OSError) as error:
            print(f"Source root was not relocated: {error}")
            return
        print(f"Relocated source root {relocation.root.name!r}; verified and updated {relocation.updated_sources} tracked source paths.")
        return

    if arguments.command == "remove-root":
        database_path = settings.data_dir / "steward.db"; initialize_database(database_path)
        roots = SourceRootRepository(database_path)
        root = roots.get_by_name(arguments.name)
        if root is None:
            print(f"No folder named {arguments.name!r} is authorized.")
            return
        if not arguments.confirm:
            print(f"This stops tracking {root.name} ({root.path}) and forgets its files in Steward. "
                  "The files themselves are not touched. Re-run with --confirm.")
            return
        removed, count = roots.remove(arguments.name)
        inbox_queue(settings).refresh()
        print(f"Stopped tracking {removed.name}; forgot {count} file(s). Nothing on disk was changed.")
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

    if arguments.command == "roots":
        database_path = settings.data_dir / "steward.db"; initialize_database(database_path)
        roots = SourceRootRepository(database_path).list_all()
        if not roots:
            print("No locally authorized source roots."); return
        for root in roots:
            excluded = ", ".join(str(item) for item in root.exclusions) or "none"
            last_scan = root.last_scanned_at.isoformat() if root.last_scanned_at is not None else "never"
            counts = (
                f"new={root.last_scan_counts[0]},updated={root.last_scan_counts[1]},"
                f"unchanged={root.last_scan_counts[2]},missing={root.last_scan_counts[3]}"
                if root.last_scan_counts is not None else "never"
            )
            print(f"{root.id}\t{root.name}\t{root.health}\tlast_scan={last_scan}\tlast_outcome={counts}\t{root.path}\texcluded={excluded}")
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
        sources = SourceRepository(database_path)
        lexical = LexicalSearchService(sources, SourceFragmentRepository(database_path))
        options = {
            "limit": arguments.limit,
            "source_types": _requested_source_types(arguments),
            "path_prefix": arguments.path_prefix,
        }
        if arguments.mode == "keyword":
            hits = lexical.search(arguments.query, **options)
        else:
            semantic = SemanticSearchService(
                sources, SQLiteSemanticIndex(database_path, SentenceTransformerEmbeddingProvider()),
            )
            searcher = semantic if arguments.mode == "meaning" else HybridRetriever(lexical, semantic)
            hits = searcher.search(arguments.query, **options)
        if not hits:
            print("No matches. If meaning search finds nothing, run `steward rebuild-semantic-index`.")
            return
        for hit in hits:
            _print_search_hit(hit)
        return

    if arguments.command == "ask":
        model_gateway = model_gateway_from_settings(settings, command="ask")
        if model_gateway is None:
            return
        graph_result = build_question_graph(
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

    if arguments.command == "telegram":
        token = os.environ.get("TELEGRAM_BOT_TOKEN")
        if not token:
            print("Set TELEGRAM_BOT_TOKEN before using `steward telegram`.")
            return
        model_gateway = model_gateway_from_settings(settings, command="telegram")
        if model_gateway is None:
            return
        database_path = settings.data_dir / "steward.db"
        application = build_telegram_application(settings, model_gateway, limit=arguments.limit)
        try:
            with telegram_runtime_lock(settings.data_dir):
                run_telegram_polling(
                    token,
                    application,
                    allowed_chat_ids=settings.telegram_allowed_chat_ids,
                    delivery_repository=TelegramUpdateDeliveryRepository(database_path),
                    callback_repository=TelegramCallbackRepository(database_path),
                    review_contexts=ReviewContextRepository(database_path),
                    message_references=MessageReferenceRepository(database_path),
                )
        except RuntimeAlreadyRunningError as error:
            print(str(error))
        return


    if arguments.command == "activity":
        database_path = settings.data_dir / "steward.db"
        initialize_database(database_path)
        for event in ActivityService(database_path).list_recent():
            print(f"{event.id}\t{event.event_type.value}\t{event.object_id or ''}\t{event.details}")
        return

    build_parser().print_help()
