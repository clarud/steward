"""File-type strategies for deterministic document text extraction."""

from __future__ import annotations

from typing import Protocol

from pypdf import PdfReader

from steward.extraction.models import ExtractionResult, SourceFragment
from steward.extraction.markdown import MarkdownExtractor
from steward.extraction.repository import SourceFragmentRepository
from steward.sources import Source, SourceType


class DocumentExtractor(Protocol):
    def extract(self, source: Source) -> ExtractionResult: ...


class PlainTextExtractor:
    def extract(self, source: Source) -> ExtractionResult:
        if source.id is None:
            raise ValueError("Only a persisted Source can be extracted.")
        text = source.path.read_text(encoding="utf-8").strip()
        fragments = () if not text else (SourceFragment(None, source.id, None, 0, text, "entire file"),)
        return ExtractionResult(source.id, fragments)


class PdfExtractor:
    def extract(self, source: Source) -> ExtractionResult:
        if source.id is None:
            raise ValueError("Only a persisted Source can be extracted.")
        if source.source_type is not SourceType.PDF:
            raise ValueError("PdfExtractor requires a PDF Source.")
        fragments = []
        for page_number, page in enumerate(PdfReader(source.path).pages, start=1):
            text = page.extract_text().strip()
            if text:
                fragments.append(SourceFragment(None, source.id, None, len(fragments), text, f"page {page_number}"))
        return ExtractionResult(source.id, tuple(fragments))


class ExtractionService:
    """Choose an extractor by Source type and persist only derived fragments."""

    def __init__(self, fragment_repository: SourceFragmentRepository) -> None:
        self._fragment_repository = fragment_repository
        self._extractors: dict[SourceType, DocumentExtractor] = {
            SourceType.MARKDOWN: MarkdownExtractor(),
            SourceType.PLAIN_TEXT: PlainTextExtractor(),
            SourceType.PDF: PdfExtractor(),
        }

    def extract_and_store(self, source: Source) -> ExtractionResult | None:
        extractor = self._extractors.get(source.source_type)
        if extractor is None:
            return None
        result = extractor.extract(source)
        self._fragment_repository.replace_for_source(result)
        return result
