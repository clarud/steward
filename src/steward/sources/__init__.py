"""Source-domain types for original evidence registered by Steward."""

from steward.sources.discovery import discover_markdown_files
from steward.sources.hashing import hash_file
from steward.sources.models import Source, SourceStatus, SourceType
from steward.sources.repository import (
    SourceAlreadyExistsError,
    SourceNotFoundError,
    SourceRepository,
)
from steward.sources.scanning import ScanResult, scan_markdown_root

__all__ = [
    "Source",
    "SourceAlreadyExistsError",
    "SourceNotFoundError",
    "SourceRepository",
    "SourceStatus",
    "SourceType",
    "ScanResult",
    "discover_markdown_files",
    "hash_file",
    "scan_markdown_root",
]
