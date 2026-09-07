"""Application configuration loaded from explicit environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

VALID_LOG_LEVELS = frozenset({"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"})
VALID_MODEL_PROVIDERS = frozenset({"gemini", "local", "openai"})


def load_environment_file() -> None:
    """Load local .env settings without overriding explicit shell variables."""
    load_dotenv(override=False)


@dataclass(frozen=True, slots=True)
class Settings:
    """Configuration shared by Steward's application boundary."""

    data_dir: Path
    inbox_dir: Path
    log_level: str
    model_provider: str
    openai_model: str | None
    gemini_model: str | None
    local_model: str | None
    local_model_url: str

    @classmethod
    def from_environment(cls) -> "Settings":
        """Load and validate settings without creating or modifying any paths."""
        data_dir = Path(os.environ.get("STEWARD_DATA_DIR", ".steward"))
        inbox_dir = Path(os.environ.get("STEWARD_INBOX_DIR", "vault/inbox"))
        log_level = os.environ.get("STEWARD_LOG_LEVEL", "INFO").upper()
        model_provider = os.environ.get("STEWARD_MODEL_PROVIDER", "gemini").casefold()
        openai_model = os.environ.get("STEWARD_OPENAI_MODEL")
        gemini_model = os.environ.get("STEWARD_GEMINI_MODEL")
        local_model = os.environ.get("STEWARD_LOCAL_MODEL")
        local_model_url = os.environ.get("STEWARD_LOCAL_MODEL_URL", "http://127.0.0.1:11434")

        if log_level not in VALID_LOG_LEVELS:
            allowed_levels = ", ".join(sorted(VALID_LOG_LEVELS))
            raise ValueError(
                f"STEWARD_LOG_LEVEL must be one of {allowed_levels}; got {log_level!r}."
            )
        if model_provider not in VALID_MODEL_PROVIDERS:
            allowed_providers = ", ".join(sorted(VALID_MODEL_PROVIDERS))
            raise ValueError(
                "STEWARD_MODEL_PROVIDER must be one of "
                f"{allowed_providers}; got {model_provider!r}."
            )

        return cls(
            data_dir=data_dir,
            inbox_dir=inbox_dir,
            log_level=log_level,
            model_provider=model_provider,
            openai_model=openai_model,
            gemini_model=gemini_model,
            local_model=local_model,
            local_model_url=local_model_url,
        )
