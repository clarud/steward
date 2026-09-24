"""Run one `steward` command."""

from __future__ import annotations

import argparse
import os
import sys
import time
import sqlite3
from collections.abc import Sequence
from datetime import datetime

from steward.config import Settings, load_environment_file
from steward.graphs.ask import run_ask
from steward.extras import MissingExtraError
from steward.runtime import RuntimeAlreadyRunningError, telegram_runtime_lock
from steward.reviews import MessageReferenceRepository, ReviewContextRepository
from steward.extraction import DocumentExtractionError, MarkdownExtractor, SourceFragmentRepository
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
from steward.activity import ActivityService, ActivityType
from steward.roots import SourceRootRepository
from steward.evaluation import (
    cites_expected, declines, evaluate_checker, evaluate_files, load_ask_cases, load_checker_cases, load_file_cases,
    load_summary_cases,
)
from steward.graphs.summarize import run_summarize
from steward.roles.checker import check_answer
from steward.roles.structured import CallBudget
from steward.graphs.find import run_find
from steward.retrieval.files import group_by_file
from steward.extraction import InvalidSearchQueryError
from steward.cli.bootstrap import (
    build_flows,
    optional_embedding_provider,
    build_telegram_application,
    filed_notices,
    rescan_all,
    scan_root,
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


def _ranker(settings: Settings, mode: str):
    """A function from query to files, best first, for one evaluation mode."""
    database_path = settings.data_dir / "steward.db"
    initialize_database(database_path)
    sources = SourceRepository(database_path)
    lexical = LexicalSearchService(sources, SourceFragmentRepository(database_path))
    if mode == "keyword":
        return lambda query: [candidate.source.path for candidate in group_by_file(_safe_search(lexical, query), "keyword")]
    embedding_provider = optional_embedding_provider()
    if mode == "hybrid":
        if embedding_provider is None:
            print("Hybrid mode needs the local embedding model: steward download-embedding-model")
            return None
        hybrid = HybridRetriever(lexical, SemanticSearchService(sources, SQLiteSemanticIndex(database_path, embedding_provider)))
        return lambda query: [candidate.source.path for candidate in group_by_file(_safe_search(hybrid, query), "hybrid")]
    model_gateway = model_gateway_from_settings(settings, command="evaluate-retrieval --mode find")
    if model_gateway is None:
        return None
    find = build_flows(settings, model_gateway, lambda source: str(source.path), embedding_provider=embedding_provider).find

    def rank(query: str):
        result = run_find(find, query)
        return [candidate.source.path for candidate, _ in result.picks] + [candidate.source.path for candidate in result.options]

    return rank


def _evaluate_checker(settings: Settings, arguments: argparse.Namespace) -> None:
    cases = load_checker_cases(arguments.cases)
    model_gateway = model_gateway_from_settings(settings, command="evaluate-checker")
    modes = [("code checks only", None)] + ([("code + model", model_gateway)] if model_gateway is not None else [])
    for label, model in modes:
        evaluation = evaluate_checker(
            lambda text, evidence: check_answer(model, CallBudget(2 if model else 0), text, evidence).text, cases,
        )
        print(
            f"\n{label}: {len(cases)} cases\n"
            f"Planted false statements removed: {evaluation.unsupported_removed}/{evaluation.unsupported} "
            f"({_share(evaluation.unsupported_removed, evaluation.unsupported)})\n"
            f"True statements kept: {evaluation.supported_kept}/{evaluation.supported} "
            f"({_share(evaluation.supported_kept, evaluation.supported)})"
        )
        for text in evaluation.missed:
            print(f"  missed (kept a false statement): {text}")
        for text in evaluation.wrongly_removed:
            print(f"  wrongly removed (a true statement): {text}")


def _evaluate_ask(settings: Settings, arguments: argparse.Namespace) -> None:
    cases = load_ask_cases(arguments.cases)
    model_gateway = model_gateway_from_settings(settings, command="evaluate-ask")
    if model_gateway is None:
        return
    flows = build_flows(settings, model_gateway, lambda source: str(source.path),
                        embedding_provider=optional_embedding_provider())
    rows, report = [], ["# Ask evaluation\n", "Mark each answer: correct, partly correct, or wrong.\n"]
    for number, case in enumerate(cases, start=1):
        started = time.perf_counter()
        result = run_ask(flows.ask, case.question)
        seconds = time.perf_counter() - started
        cited = list(dict.fromkeys(item.source.path for item in result.cited))
        good = cites_expected(cited, case.expected) if case.expected else declines(result.status, result.text, cited)
        rows.append((case, result.status, good, result.removed, result.calls, seconds))
        verdict = ("cites an expected file" if good else "does not cite an expected file") if case.expected else (
            "declined, as it should" if good else "answered, but the files don't cover this")
        print(f"{number:>2}. {'ok  ' if good else 'MISS'} {case.question} ({verdict}; {result.calls} calls, {seconds:.1f}s)")
        report += [
            f"## {number}. {case.question}\n",
            f"Status: {result.status} · removed {result.removed} · {result.calls} calls · {seconds:.1f}s · {verdict}\n",
            result.text + "\n",
            "Sources: " + ("; ".join(path.name for path in cited) or "none") + "\n",
            "Correct? [ ] yes  [ ] partly  [ ] no\n",
        ]
    answerable = [row for row in rows if row[0].expected]
    unanswerable = [row for row in rows if not row[0].expected]
    print(f"\nCases: {len(rows)}")
    if answerable:
        print(f"Answerable, cites an expected file: {sum(row[2] for row in answerable)}/{len(answerable)} "
              f"({_share(sum(row[2] for row in answerable), len(answerable))})")
    if unanswerable:
        print(f"Not answerable, correctly declined: {sum(row[2] for row in unanswerable)}/{len(unanswerable)} "
              f"({_share(sum(row[2] for row in unanswerable), len(unanswerable))})")
    print(f"Statements removed by the checker: {sum(row[3] for row in rows)}")
    print(f"Mean model calls: {sum(row[4] for row in rows) / len(rows):.1f} · "
          f"mean time: {sum(row[5] for row in rows) / len(rows):.1f}s")
    _write_report(arguments.report, report)


def _evaluate_summaries(settings: Settings, arguments: argparse.Namespace) -> None:
    files = load_summary_cases(arguments.cases)
    model_gateway = model_gateway_from_settings(settings, command="evaluate-summaries")
    if model_gateway is None:
        return
    database_path = settings.data_dir / "steward.db"
    flows = build_flows(settings, model_gateway, lambda source: str(source.path))
    active = SourceRepository(database_path).list_active()
    rows, report = [], ["# Summary evaluation\n", "For each: is it accurate, and is anything important missing?\n"]
    for number, ending in enumerate(files, start=1):
        source = next((item for item in active if item.path.as_posix().casefold().endswith(ending.casefold())), None)
        if source is None:
            print(f"{number:>2}. not indexed: {ending}")
            continue
        with sqlite3.connect(database_path) as connection:  # measure a fresh summary, not the cache
            connection.execute("DELETE FROM source_summaries WHERE source_id = ?", (source.id,))
        started = time.perf_counter()
        result = run_summarize(flows.summarize, source.id or 0)
        seconds = time.perf_counter() - started
        share = result.covered / result.total if result.total else 0.0
        rows.append((result, share, seconds))
        print(f"{number:>2}. {result.status:<10} covered {result.covered}/{result.total} ({share:.0%}) · "
              f"{result.calls} calls · {seconds:.0f}s · {source.path.name}"
              + (f" · skipped {len(result.skipped)}" if result.skipped else ""))
        report += [
            f"## {number}. {source.path.name}\n",
            f"Status: {result.status} · covered {result.covered}/{result.total} ({share:.0%}) · "
            f"{result.calls} calls · {seconds:.0f}s\n",
            result.text + "\n",
            "Accurate? [ ] yes  [ ] mostly  [ ] no    Missing anything important? ______\n",
        ]
    if rows:
        completed = [row for row in rows if row[0].status in {"done", "partial", "notes_only"}]
        print(f"\nFiles: {len(rows)} · completed: {len(completed)}/{len(rows)}")
        if completed:
            print(f"Mean coverage: {sum(row[1] for row in completed) / len(completed):.0%} · "
                  f"mean calls: {sum(row[0].calls for row in completed) / len(completed):.1f} · "
                  f"mean time: {sum(row[2] for row in completed) / len(completed):.0f}s")
    _write_report(arguments.report, report)


def _share(part: int, whole: int) -> str:
    return f"{part / whole:.0%}" if whole else "n/a"


def _write_report(path, lines: list[str]) -> None:
    if path is not None:
        path.write_text("\n".join(lines), encoding="utf-8")
        print(f"Report written to {path}")


def _safe_search(searcher, query: str):
    try:
        return searcher.search(query, limit=20)
    except (InvalidSearchQueryError, ValueError):
        return ()


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
        database_paths = (settings.data_dir / "steward.db",)
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
            result, moves = scan_root(settings, root)
        except sqlite3.Error as error:
            _print_scan_database_error("Root scan", error)
            return
        print(
            f"Scan complete for {root.name}: "
            f"new={result.new} updated={result.updated} "
            f"unchanged={result.unchanged} missing={result.missing}"
        )
        for move in moves:
            print(f"Moved: {move.previous_path.name} -> {move.source.path}")
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
            result, _ = scan_root(settings, root)
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
        if not arguments.cases.is_file():
            print(f"Case file does not exist: {arguments.cases}")
            return
        try:
            cases = load_file_cases(arguments.cases)
        except ValueError as error:
            print(f"Invalid case file: {error}")
            return
        rank = _ranker(settings, arguments.mode)
        if rank is None:
            return
        evaluation = evaluate_files(rank, cases)
        print(
            f"Mode: {arguments.mode}\nCases: {evaluation.case_count}\n"
            f"Hit@1: {evaluation.hit_at_1:.0%}\nHit@3: {evaluation.hit_at_3:.0%}\n"
            f"MRR: {evaluation.mean_reciprocal_rank:.3f}"
        )
        if evaluation.misses:
            print("Not in the top 3:")
            for query, expected in evaluation.misses:
                print(f"- {query} -> {expected}")
        return

    if arguments.command in {"evaluate-checker", "evaluate-ask", "evaluate-summaries"}:
        if not arguments.cases.is_file():
            print(f"Case file does not exist: {arguments.cases}")
            return
        try:
            {"evaluate-checker": _evaluate_checker, "evaluate-ask": _evaluate_ask,
             "evaluate-summaries": _evaluate_summaries}[arguments.command](settings, arguments)
        except ValueError as error:
            print(f"Invalid case file: {error}")
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
        flows = build_flows(
            settings, model_gateway, lambda source: str(source.path),
            embedding_provider=optional_embedding_provider(),
        )
        result = run_ask(flows.ask, arguments.question)
        print(result.text)
        if result.removed:
            print(f"({result.removed} statement(s) removed: not supported by your files.)")
        if result.cited:
            print("\nSources:")
            for item in result.cited:
                print(f"[{item.key}] {item.source.path}:{item.fragment.location}")
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
        embedding_provider = optional_embedding_provider()
        application = build_telegram_application(settings, model_gateway, embedding_provider=embedding_provider)
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
                    periodic=lambda: filed_notices(
                        settings, rescan_all(settings, embedding_provider=embedding_provider)
                    ),
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
