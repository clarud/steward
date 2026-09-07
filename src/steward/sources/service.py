"""Application service for source registration and derived extraction."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from steward.extraction import MarkdownExtractor, SourceFragmentRepository
from steward.sources.models import SourceType
from steward.sources.models import Source, SourceStatus
from steward.sources.hashing import hash_file
from steward.sources.repository import SourceRepository
from steward.sources.scanning import ScanResult, scan_markdown_root

if TYPE_CHECKING:
    from steward.retrieval.semantic import SemanticIndex


class SourceService:
    """Coordinate source scanning with refresh of derived Markdown fragments."""

    def __init__(
        self,
        source_repository: SourceRepository,
        fragment_repository: SourceFragmentRepository,
        markdown_extractor: MarkdownExtractor,
        semantic_index: SemanticIndex | None = None,
    ) -> None:
        self._source_repository = source_repository
        self._fragment_repository = fragment_repository
        self._markdown_extractor = markdown_extractor
        self._semantic_index = semantic_index

    def scan_markdown_root(self, root: Path) -> ScanResult:
        """Synchronize Source metadata, then refresh active Markdown fragments."""
        result = scan_markdown_root(root, self._source_repository)
        resolved_root = root.resolve()

        for source in self._source_repository.list_active():
            if (
                source.source_type is SourceType.MARKDOWN
                and source.path.is_relative_to(resolved_root)
            ):
                extraction_result = self._markdown_extractor.extract(source)
                fragments = self._fragment_repository.replace_for_source(extraction_result)
                if self._semantic_index is not None:
                    self._semantic_index.replace_for_source(fragments)

        return result

    def refresh_markdown_path(self, path: Path) -> str:
        """Hash one watched path and re-extract only when its actual content changed."""
        path = path.resolve()
        existing = self._source_repository.get_by_path(path)
        if not path.is_file():
            if existing is not None and existing.status is SourceStatus.ACTIVE:
                self._source_repository.update(replace(existing, status=SourceStatus.MISSING, last_seen_at=datetime.now(UTC)))
                return "missing"
            return "ignored"
        if path.suffix.casefold() != ".md":
            return "ignored"
        stat = path.stat()
        content_hash = hash_file(path)
        now = datetime.now(UTC)
        modified_at = datetime.fromtimestamp(stat.st_mtime, tz=UTC)
        if existing is None:
            source = self._source_repository.add(Source(None, path, content_hash, SourceType.MARKDOWN, stat.st_size, modified_at, now, now))
            outcome = "new"
        else:
            if existing.content_hash == content_hash and existing.status is SourceStatus.ACTIVE:
                return "unchanged"
            source = replace(existing, content_hash=content_hash, size_bytes=stat.st_size, modified_at=modified_at, last_seen_at=now, status=SourceStatus.ACTIVE)
            self._source_repository.update(source)
            outcome = "updated"
        fragments = self._fragment_repository.replace_for_source(self._markdown_extractor.extract(source))
        if self._semantic_index is not None:
            self._semantic_index.replace_for_source(fragments)
        return outcome
