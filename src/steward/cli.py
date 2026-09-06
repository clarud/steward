"""Command-line entry point for Steward."""

from __future__ import annotations

import argparse
import logging
from collections.abc import Sequence
from pathlib import Path

from steward.config import Settings
from steward.extraction import MarkdownExtractor, SourceFragmentRepository
from steward.logging import configure_logging
from steward.sources import SourceRepository
from steward.sources.service import SourceService
from steward.storage import initialize_database
from steward.retrieval import LexicalSearchService


def build_parser() -> argparse.ArgumentParser:
    """Create the command-line interface for currently available features."""
    parser = argparse.ArgumentParser(prog="steward")
    subcommands = parser.add_subparsers(dest="command")
    scan_parser = subcommands.add_parser("scan", help="Register Markdown files under a root")
    scan_parser.add_argument("root", type=Path, help="Directory containing Markdown files")
    subcommands.add_parser("sources", help="List registered sources")
    search_parser = subcommands.add_parser("search", help="Search indexed Markdown fragments")
    search_parser.add_argument("query", help="Terms to search for")
    search_parser.add_argument("--limit", type=int, default=5, help="Maximum matches")
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    """Run a Steward command."""
    arguments = build_parser().parse_args(argv)
    settings = Settings.from_environment()
    configure_logging(settings)

    if arguments.command == "scan":
        database_path = settings.data_dir / "steward.db"
        initialize_database(database_path)
        result = SourceService(
            source_repository=SourceRepository(database_path),
            fragment_repository=SourceFragmentRepository(database_path),
            markdown_extractor=MarkdownExtractor(),
        ).scan_markdown_root(arguments.root)
        print(
            "Scan complete: "
            f"new={result.new} updated={result.updated} "
            f"unchanged={result.unchanged} missing={result.missing}"
        )
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

    if arguments.command == "search":
        database_path = settings.data_dir / "steward.db"
        initialize_database(database_path)
        hits = LexicalSearchService(
            source_repository=SourceRepository(database_path),
            fragment_repository=SourceFragmentRepository(database_path),
        ).search(arguments.query, limit=arguments.limit)
        if not hits:
            print("No matching fragments.")
            return

        for hit in hits:
            heading = hit.fragment.heading or "Preamble"
            snippet = " ".join(hit.fragment.text.split())
            print(f"{hit.source.path}:{hit.fragment.location} [{heading}]")
            print(f"  {snippet[:160]}")
        return

    logging.getLogger(__name__).info("Steward foundation started")
    build_parser().print_help()


if __name__ == "__main__":
    main()
