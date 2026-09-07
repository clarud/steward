"""Domain model for original files known to Steward."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path


class SourceType(StrEnum):
    """File types Steward currently knows how to register."""

    MARKDOWN = "markdown"
    PDF = "pdf"
    BINARY = "binary"


class SourceStatus(StrEnum):
    """Whether a registered source is present at its last known path."""

    ACTIVE = "active"
    MISSING = "missing"


@dataclass(frozen=True, slots=True)
class Source:
    """Metadata for one physical original file, not its extracted content."""

    id: int | None
    path: Path
    content_hash: str
    source_type: SourceType
    size_bytes: int
    modified_at: datetime
    first_seen_at: datetime
    last_seen_at: datetime
    status: SourceStatus = SourceStatus.ACTIVE

    def __post_init__(self) -> None:
        if self.id is not None and self.id <= 0:
            raise ValueError("Source id must be positive.")
        if not str(self.path) or self.path == Path("."):
            raise ValueError("Source path must not be empty.")
        if self.size_bytes < 0:
            raise ValueError("Source size_bytes must not be negative.")
        if len(self.content_hash) != 64 or any(
            character not in "0123456789abcdef" for character in self.content_hash
        ):
            raise ValueError("Source content_hash must be a lowercase SHA-256 hex digest.")
        if self.first_seen_at.tzinfo is None or self.last_seen_at.tzinfo is None:
            raise ValueError("Source scan timestamps must be timezone-aware.")
        if self.first_seen_at > self.last_seen_at:
            raise ValueError("Source first_seen_at must not be after last_seen_at.")
