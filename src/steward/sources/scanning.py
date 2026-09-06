"""Synchronize Markdown files under a root with Steward's Source Registry."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path

from steward.sources.discovery import discover_markdown_files
from steward.sources.hashing import hash_file
from steward.sources.models import Source, SourceStatus, SourceType
from steward.sources.repository import SourceRepository


@dataclass(frozen=True, slots=True)
class ScanResult:
    """A summary of one source-registry synchronization run."""

    new: int
    updated: int
    unchanged: int
    missing: int


def scan_markdown_root(
    root: Path,
    repository: SourceRepository,
    *,
    scanned_at: datetime | None = None,
) -> ScanResult:
    """Register, refresh, and mark missing Markdown sources beneath root."""
    if scanned_at is None:
        scanned_at = datetime.now(UTC)
    if scanned_at.tzinfo is None:
        raise ValueError("scanned_at must be timezone-aware.")

    resolved_root = root.resolve()
    discovered_paths = discover_markdown_files(resolved_root)
    discovered_path_set = set(discovered_paths)
    new_count = updated_count = unchanged_count = 0

    for path in discovered_paths:
        stat = path.stat()
        content_hash = hash_file(path)
        modified_at = datetime.fromtimestamp(stat.st_mtime, tz=UTC)
        existing_source = repository.get_by_path(path)

        if existing_source is None:
            repository.add(
                Source(
                    id=None,
                    path=path,
                    content_hash=content_hash,
                    source_type=SourceType.MARKDOWN,
                    size_bytes=stat.st_size,
                    modified_at=modified_at,
                    first_seen_at=scanned_at,
                    last_seen_at=scanned_at,
                )
            )
            new_count += 1
            continue

        was_unchanged = (
            existing_source.content_hash == content_hash
            and existing_source.size_bytes == stat.st_size
            and existing_source.modified_at == modified_at
            and existing_source.status is SourceStatus.ACTIVE
        )
        refreshed_source = replace(
            existing_source,
            content_hash=content_hash,
            size_bytes=stat.st_size,
            modified_at=modified_at,
            last_seen_at=scanned_at,
            status=SourceStatus.ACTIVE,
        )
        repository.update(refreshed_source)

        if was_unchanged:
            unchanged_count += 1
        else:
            updated_count += 1

    missing_count = 0
    for active_source in repository.list_active():
        if (
            active_source.path.is_relative_to(resolved_root)
            and active_source.path not in discovered_path_set
        ):
            repository.update(
                replace(
                    active_source,
                    status=SourceStatus.MISSING,
                    last_seen_at=scanned_at,
                )
            )
            missing_count += 1

    return ScanResult(
        new=new_count,
        updated=updated_count,
        unchanged=unchanged_count,
        missing=missing_count,
    )
