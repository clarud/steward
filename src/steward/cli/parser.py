"""The `steward` command-line parser."""

from __future__ import annotations

import argparse
from pathlib import Path

from steward.sources import SourceType
from steward.privacy import PrivacyRule


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


def build_parser() -> argparse.ArgumentParser:
    """Create Steward's command-line interface."""
    parser = argparse.ArgumentParser(prog="steward")
    subcommands = parser.add_subparsers(dest="command")
    scan_parser = subcommands.add_parser("scan", help="Register supported source files under a root")
    scan_parser.add_argument("root", type=Path, help="Directory containing supported source files")
    watch_parser = subcommands.add_parser("watch", help="Watch supported source files and incrementally refresh changes (full scans reconcile moves)")
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
    root_onboard = subcommands.add_parser(
        "onboard-root",
        help="Locally authorize and scan an existing directory in one step",
    )
    root_onboard.add_argument("name", help="Human-readable name for this local source root")
    root_onboard.add_argument("path", type=Path, help="Existing directory to authorize and scan in place")
    root_onboard.add_argument(
        "--exclude", action="append", type=Path, default=[],
        help="Root-relative directory to exclude from this root (repeatable)",
    )
    root_relocate = subcommands.add_parser("relocate-root", help="Rebind a missing root after verifying tracked source hashes")
    root_relocate.add_argument("name", help="Existing authorized source-root name")
    root_relocate.add_argument("path", type=Path, help="Existing replacement directory")
    root_relocate.add_argument("--confirm", action="store_true", help="Confirm the authorization and registered-path change")
    root_profile = subcommands.add_parser("set-root-profile", help="Store owner-reviewed descriptive metadata for an authorized root")
    root_profile.add_argument("name", help="Authorized source-root name")
    root_profile.add_argument("--purpose", required=True, help="Short owner-reviewed description")
    root_profile.add_argument("--guidance", action="append", type=Path, default=[], help="Existing root-relative guidance file (repeatable)")
    root_profile.add_argument("--tier", action="append", default=[], help="Authority-tier label, highest first (repeatable)")
    subcommands.add_parser("roots", help="List locally authorized source roots")
    health_parser = subcommands.add_parser(
        "health", help="Report safe local runtime health without exposing paths or secrets"
    )
    health_parser.add_argument("--strict", action="store_true", help="Exit 1 when local Telegram prerequisites are unavailable (does not test network services)")
    scan_root_parser = subcommands.add_parser("scan-root", help="Scan one locally authorized source root")
    scan_root_parser.add_argument("name", help="Authorized source-root name")
    reconcile_moves_parser = subcommands.add_parser(
        "reconcile-moves", help="List reviewable same-root source move matches"
    )
    reconcile_moves_parser.add_argument("name", help="Authorized source-root name")
    review_move_parser = subcommands.add_parser(
        "review-move", help="Accept or reject one reviewed source move match"
    )
    review_move_parser.add_argument("proposal_id", type=int)
    review_move_group = review_move_parser.add_mutually_exclusive_group(required=True)
    review_move_group.add_argument("--accept", action="store_true")
    review_move_group.add_argument("--reject", action="store_true")
    handoff_parser = subcommands.add_parser("codex-handoff", help="Prepare a local metadata-only manifest for selected sources")
    handoff_parser.add_argument("source_ids", type=int, nargs="+", help="Active source IDs to include")
    handoff_parser.add_argument("--note", default="", help="Optional user guidance for Codex; stored locally in the manifest")
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
        "semantic-search", help="Search extracted source fragments by meaning"
    )
    semantic_parser.add_argument("query", help="A natural-language question or phrase")
    semantic_parser.add_argument("--limit", type=int, default=5, help="Maximum matches")
    _add_source_type_filter(semantic_parser)
    hybrid_parser = subcommands.add_parser(
        "hybrid-search", help="Combine lexical and semantic source search"
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
        "agent", help="Answer using allowlisted local source-retrieval tools"
    )
    agent_parser.add_argument("question", help="Question about saved local sources")
    agent_parser.add_argument("--thread-id", default="cli:agent", help="Persistent LangGraph thread ID")
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
    privacy_parser = subcommands.add_parser("set-source-privacy", help="Set a source's model privacy rule")
    privacy_parser.add_argument("source_id", type=int)
    privacy_parser.add_argument("rule", choices=[rule.value for rule in PrivacyRule])
    source_privacy_parser = subcommands.add_parser("source-privacy", help="Show a source's privacy rule")
    source_privacy_parser.add_argument("source_id", type=int)
    subcommands.add_parser("activity", help="List recent activity events")
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
