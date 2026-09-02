"""Command-line entry point for Steward."""

from __future__ import annotations

import logging

from steward.config import Settings
from steward.logging import configure_logging


def main() -> None:
    """Start the minimal Phase 0 application boundary."""
    settings = Settings.from_environment()
    configure_logging(settings)
    logging.getLogger(__name__).info("Steward foundation started")
    print("Steward foundation initialized.")


if __name__ == "__main__":
    main()

