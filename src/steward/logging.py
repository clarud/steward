"""Central logging setup for the Steward application."""

from __future__ import annotations

import logging

from steward.config import Settings


def configure_logging(settings: Settings) -> None:
    """Configure human-readable process logging from validated settings."""
    logging.basicConfig(
        level=settings.log_level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

