"""Application service for source registration and derived extraction."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from steward.extraction import MarkdownExtractor, SourceFragmentRepository
from steward.sources.models import SourceType
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
