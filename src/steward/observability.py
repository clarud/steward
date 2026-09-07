"""Small structured local traces for inspecting Steward workflows."""

from __future__ import annotations

import json
import logging


LOGGER = logging.getLogger("steward.trace")


def trace(event: str, **fields: object) -> None:
    """Emit a machine-readable local trace without logging model/source text."""

    LOGGER.info(json.dumps({"event": event, **fields}, sort_keys=True, default=str))
