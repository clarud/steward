"""Application configuration loaded from explicit environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

VALID_LOG_LEVELS = frozenset({"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"})


@dataclass(frozen=True, slots=True)
class Settings:
    """Configuration shared by Steward's application boundary."""

    data_dir: Path
    log_level: str

    @classmethod
    def from_environment(cls) -> "Settings":
        """Load and validate settings without creating or modifying any paths."""
        data_dir = Path(os.environ.get("STEWARD_DATA_DIR", ".steward"))
        log_level = os.environ.get("STEWARD_LOG_LEVEL", "INFO").upper()

        if log_level not in VALID_LOG_LEVELS:
            allowed_levels = ", ".join(sorted(VALID_LOG_LEVELS))
            raise ValueError(
                f"STEWARD_LOG_LEVEL must be one of {allowed_levels}; got {log_level!r}."
            )

        return cls(data_dir=data_dir, log_level=log_level)

