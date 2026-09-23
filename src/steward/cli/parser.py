"""The `steward` command-line parser."""

from __future__ import annotations

import argparse
from pathlib import Path

from steward.sources import SourceType


def build_parser() -> argparse.ArgumentParser:
    """Create Steward's command-line interface."""
    parser = argparse.ArgumentParser(prog="steward")
    commands = parser.add_subparsers(dest="command")

    # Folders
    onboard = commands.add_parser("onboard-root", help="Authorize a folder and scan it in place")
    onboard.add_argument("name", help="Short name for the folder, e.g. Y4S1")
    onboard.add_argument("path", type=Path, help="Existing folder to index; nothing is copied or moved")
    onboard.add_argument("--exclude", action="append", type=Path, default=[], help="Subfolder to skip (repeatable)")
    scan = commands.add_parser("scan-root", help="Rescan a folder now (the Telegram bot also rescans every 15 minutes)")
    scan.add_argument("name")
    commands.add_parser("roots", help="List authorized folders and their last scan")
    relocate = commands.add_parser("relocate-root", help="Point a folder that moved at its new location")
    relocate.add_argument("name")
    relocate.add_argument("path", type=Path)
    relocate.add_argument("--confirm", action="store_true")
    remove = commands.add_parser("remove-root", help="Stop tracking a folder; its files are not touched")
    remove.add_argument("name")
    remove.add_argument("--confirm", action="store_true")

    # Finding and answering
    search = commands.add_parser("search", help="Find files by words or meaning")
    search.add_argument("query")
    search.add_argument("--mode", choices=("hybrid", "keyword", "meaning"), default="hybrid")
    search.add_argument("--limit", type=int, default=5)
    search.add_argument(
        "--type", dest="source_types", action="append",
        choices=sorted(item.value for item in SourceType if item is not SourceType.BINARY),
        help="Only this file type (repeatable)",
    )
    search.add_argument("--path-prefix", type=Path, help="Only files under this folder")
    ask = commands.add_parser("ask", help="Answer a question from your files, with sources")
    ask.add_argument("question")
    commands.add_parser("inbox", help="Refresh INBOX.md and list what's waiting to be filed")

    # Upkeep
    reextract = commands.add_parser("reextract", help="Extract one file's text again")
    reextract.add_argument("source_id", type=int)
    unregister = commands.add_parser("unregister-source", help="Forget one file; the original is not touched")
    unregister.add_argument("source_id", type=int)
    unregister.add_argument("--confirm", action="store_true")
    commands.add_parser("download-embedding-model", help="Download the local model for meaning-based search")
    commands.add_parser("rebuild-semantic-index", help="Rebuild meaning-search vectors for every file")
    evaluate = commands.add_parser("evaluate-retrieval", help="Score finding files: hit@1, hit@3, MRR")
    evaluate.add_argument("cases", type=Path, help="YAML: cases: [{query: ..., file: CS3210/tut04.pdf}]")
    evaluate.add_argument("--mode", choices=("keyword", "hybrid", "find"), default="keyword",
                          help="find runs the multi-agent Find flow and uses the configured model")
    commands.add_parser("activity", help="Show recent activity")

    # Running
    commands.add_parser("telegram", help="Run the Telegram bot on this computer")
    health = commands.add_parser("health", help="Check local readiness without showing paths or secrets")
    health.add_argument("--strict", action="store_true", help="Exit 1 if the bot isn't ready to run")
    backup = commands.add_parser("backup", help="Snapshot Steward's databases")
    backup.add_argument("--destination", type=Path, help="New folder for the snapshots")
    restore = commands.add_parser("restore", help="Restore a database snapshot (makes a safety copy first)")
    restore.add_argument("--snapshot", type=Path, required=True)
    restore.add_argument("--destination", type=Path, required=True)
    restore.add_argument("--safety-backup", type=Path, required=True)
    restore.add_argument("--confirm", action="store_true")
    return parser
