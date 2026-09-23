"""Application service for source registration and derived extraction."""

from __future__ import annotations

import logging
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from steward.extraction import (
    ExtractionResult,
    ExtractionService,
    DocumentExtractionError,
    MarkdownExtractor,
    SourceFragment,
    SourceFragmentRepository,
)
from steward.sources.models import SourceType
from steward.sources.models import Source, SourceStatus
from steward.sources.hashing import hash_file
from steward.sources.repository import SourceRepository
from steward.sources.scanning import ScanResult, scan_markdown_root, scan_source_root
from steward.sources.discovery import DEFAULT_EXCLUDED_DIRECTORY_NAMES, source_type_for_path

if TYPE_CHECKING:
    from steward.retrieval.semantic import SemanticIndex


logger = logging.getLogger(__name__)


class SourceService:
    """Coordinate scanning and refresh derived fragments only when content changes."""

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
        self._document_extraction = ExtractionService(fragment_repository)
        self._semantic_index = semantic_index

    def scan_markdown_root(self, root: Path) -> ScanResult:
        """Synchronize metadata, then refresh changed Markdown fragments only."""
        before = self._active_content_hashes()
        result = scan_markdown_root(root, self._source_repository)
        resolved_root = root.resolve()

        for source in self._source_repository.list_active():
            if (
                source.source_type is SourceType.MARKDOWN
                and source.path.is_relative_to(resolved_root)
                and self._needs_extraction(source, before)
            ):
                extraction_result = self._markdown_extractor.extract(source)
                fragments = self._fragment_repository.replace_for_source(extraction_result)
                if self._semantic_index is not None:
                    self._semantic_index.replace_for_source(fragments)

        return result

    def scan_source_root(self, root: Path, *, exclusions: tuple[Path, ...] = ()) -> ScanResult:
        """Synchronize and extract only new, changed, or restored vault sources."""

        before = self._active_content_hashes()
        result = scan_source_root(root, self._source_repository, exclusions=exclusions)
        resolved_root = root.resolve()
        excluded_paths = tuple((item if item.is_absolute() else resolved_root / item).resolve() for item in exclusions)
        for source in self._source_repository.list_active():
            if not source.path.is_relative_to(resolved_root) or any(source.path.is_relative_to(item) for item in excluded_paths) or any(parent.name in DEFAULT_EXCLUDED_DIRECTORY_NAMES for parent in source.path.parents):
                continue
            if not self._needs_extraction(source, before):
                continue
            try:
                if source.source_type is SourceType.MARKDOWN:
                    fragments = self._fragment_repository.replace_for_source(
                        self._markdown_extractor.extract(source)
                    )
                elif source.source_type in {
                    SourceType.PLAIN_TEXT,
                    SourceType.PDF,
                    SourceType.DOCX,
                    SourceType.HTML,
                    SourceType.IMAGE,
                }:
                    self._document_extraction.extract_and_store(source)
                    fragments = self._fragment_repository.list_for_source(source.id or 0)
                else:
                    continue
            except (OSError, UnicodeDecodeError, DocumentExtractionError) as error:
                # The original remains registered.  Derived text is removed so a
                # changed-but-unreadable file cannot remain searchable as its old content.
                logger.warning("Could not extract %s: %s", source.path, error)
                if source.id is None:
                    raise RuntimeError("Active sources must have an ID.") from error
                fragments = self._fragment_repository.replace_for_source(
                    ExtractionResult(source_id=source.id, fragments=())
                )
            if self._semantic_index is not None:
                self._semantic_index.replace_for_source(fragments)
        return result

    def _active_content_hashes(self) -> dict[Path, str]:
        """Snapshot source content before a scan, not mutable file timestamps."""

        return {
            source.path: source.content_hash
            for source in self._source_repository.list_active()
        }

    @staticmethod
    def _needs_extraction(source: Source, previous_hashes: dict[Path, str]) -> bool:
        """Derived text is valid until a new original hash replaces it.

        A source missing from the pre-scan snapshot is either newly discovered
        or restored after being missing. Both need derived data. A timestamp-only
        touch retains the same hash and does not justify repeating OCR or a
        parser invocation.
        """

        return previous_hashes.get(source.path) != source.content_hash

    def refresh_markdown_path(self, path: Path) -> str:
        """Compatibility wrapper for callers that explicitly watch Markdown."""

        if path.suffix.casefold() != ".md":
            return "ignored"
        return self.refresh_source_path(path)

    def refresh_source_path(self, path: Path) -> str:
        """Hash and refresh one supported watched source.

        A watcher notification is intentionally only a hint: it can be repeated,
        arrive after a delete, or represent a metadata-only touch. The stored
        content hash decides whether derived fragments must change. Full scans
        remain responsible for reconciling missed events and moves.
        """

        path = path.resolve()
        existing = self._source_repository.get_by_path(path)
        if not path.is_file():
            if existing is not None and existing.status is SourceStatus.ACTIVE:
                self._source_repository.update(replace(existing, status=SourceStatus.MISSING, last_seen_at=datetime.now(UTC)))
                return "missing"
            return "ignored"
        source_type = source_type_for_path(path)
        if source_type is None:
            return "ignored"
        stat = path.stat()
        content_hash = hash_file(path)
        now = datetime.now(UTC)
        modified_at = datetime.fromtimestamp(stat.st_mtime, tz=UTC)
        if existing is None:
            source = self._source_repository.add(Source(None, path, content_hash, source_type, stat.st_size, modified_at, now, now))
            outcome = "new"
        else:
            if existing.content_hash == content_hash and existing.status is SourceStatus.ACTIVE:
                return "unchanged"
            source = replace(existing, content_hash=content_hash, source_type=source_type, size_bytes=stat.st_size, modified_at=modified_at, last_seen_at=now, status=SourceStatus.ACTIVE)
            self._source_repository.update(source)
            outcome = "updated"
        fragments = self._extract_source(source)
        if self._semantic_index is not None:
            self._semantic_index.replace_for_source(fragments)
        return outcome

    def _extract_source(self, source: Source) -> tuple[SourceFragment, ...]:
        """Refresh derived fragments for one registered supported source."""

        try:
            if source.source_type is SourceType.MARKDOWN:
                return self._fragment_repository.replace_for_source(
                    self._markdown_extractor.extract(source)
                )
            self._document_extraction.extract_and_store(source)
            return self._fragment_repository.list_for_source(source.id or 0)
        except (OSError, UnicodeDecodeError, DocumentExtractionError) as error:
            logger.warning("Could not extract %s: %s", source.path, error)
            if source.id is None:
                raise RuntimeError("Active sources must have an ID.") from error
            return self._fragment_repository.replace_for_source(
                ExtractionResult(source_id=source.id, fragments=())
            )

    def reextract_source(self, source_id: int) -> tuple[SourceFragment, ...]:
        """Deliberately rebuild one source's derived text and optional vectors.

        This is intentionally separate from a normal hash-based scan: calling
        it explicitly is how an improved parser is applied to an unchanged
        canonical original.
        """

        source = self._source_repository.get_by_id(source_id)
        if source is None:
            raise ValueError(f"Source {source_id} was not found.")
        if source.status is not SourceStatus.ACTIVE:
            raise ValueError(f"Source {source_id} is not active at its recorded path.")
        if source.source_type is SourceType.MARKDOWN:
            fragments = self._fragment_repository.replace_for_source(
                self._markdown_extractor.extract(source)
            )
        else:
            result = self._document_extraction.extract_and_store(source)
            if result is None:
                raise ValueError(f"Source {source_id} has no supported text extractor.")
            fragments = self._fragment_repository.list_for_source(source_id)
        if self._semantic_index is not None:
            self._semantic_index.replace_for_source(fragments)
        return fragments

    def rebuild_semantic_index(self) -> int:
        """Regenerate vectors from current fragments without reparsing originals."""

        if self._semantic_index is None:
            raise ValueError("A semantic index is required to rebuild vectors.")
        self._semantic_index.clear()
        indexed = 0
        for source in self._source_repository.list_active():
            fragments = self._fragment_repository.list_for_source(source.id or 0)
            if not fragments:
                continue
            self._semantic_index.replace_for_source(fragments)
            indexed += len(fragments)
        return indexed
