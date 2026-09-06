"""Command-line entry point for Steward."""

from __future__ import annotations

import argparse
import logging
from collections.abc import Sequence
from pathlib import Path

from steward.config import Settings
from steward.logging import configure_logging
from steward.sources import SourceRepository, scan_markdown_root
from steward.storage import initialize_database


def build_parser() -> argparse.ArgumentParser:
    """Create the command-line interface for currently available features."""
    parser = argparse.ArgumentParser(prog="steward")
    subcommands = parser.add_subparsers(dest="command")
    scan_parser = subcommands.add_parser("scan", help="Register Markdown files under a root")
    scan_parser.add_argument("root", type=Path, help="Directory containing Markdown files")
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    """Run a Steward command."""
    arguments = build_parser().parse_args(argv)
    settings = Settings.from_environment()
    configure_logging(settings)

    if arguments.command == "scan":
        database_path = settings.data_dir / "steward.db"
        initialize_database(database_path)
        result = scan_markdown_root(arguments.root, SourceRepository(database_path))
        print(
            "Scan complete: "
            f"new={result.new} updated={result.updated} "
            f"unchanged={result.unchanged} missing={result.missing}"
        )
        return

    logging.getLogger(__name__).info("Steward foundation started")
    build_parser().print_help()


if __name__ == "__main__":
    main()
