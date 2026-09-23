"""Source-domain types for original evidence registered by Steward."""

from steward.sources.discovery import discover_markdown_files, discover_source_files, source_type_for_path
from steward.sources.hashing import hash_file
from steward.sources.models import Source, SourceStatus, SourceType
from steward.sources.repository import (
    SourceAlreadyExistsError,
    SourceNotFoundError,
    SourceRepository,
)
from steward.sources.scanning import ScanResult, scan_markdown_root, scan_source_root
from steward.sources.moves import SourceMoveProposal, SourceMoveProposalRepository, SourceMoveReconciliationService
from steward.sources.inbox_queue import InboxQueue, InboxQueueEntry
from steward.sources.inbox_context import SourceInboxContext, SourceInboxContextRepository

__all__ = [
    "Source",
    "SourceAlreadyExistsError",
    "SourceNotFoundError",
    "SourceMoveProposal",
    "SourceMoveProposalRepository",
    "SourceMoveReconciliationService",
    "InboxQueue",
    "InboxQueueEntry",
    "SourceInboxContext",
    "SourceInboxContextRepository",
    "SourceRepository",
    "SourceStatus",
    "SourceType",
    "ScanResult",
    "discover_markdown_files",
    "discover_source_files",
    "hash_file",
    "scan_markdown_root",
    "scan_source_root",
    "source_type_for_path",
]
