"""Command-line entry point for Steward."""

from steward.cli.commands import main
from steward.cli.parser import build_parser

__all__ = ["build_parser", "main"]
