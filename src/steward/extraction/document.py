"""File-type strategies for deterministic document text extraction."""

from __future__ import annotations

from typing import Protocol
from zipfile import BadZipFile

from pypdf import PdfReader
from pypdf.errors import PyPdfError
from docx import Document
from docx.opc.exceptions import OpcError

from steward.extraction.models import ExtractionResult, SourceFragment
from steward.extraction.markdown import MarkdownExtractor
from steward.extraction.repository import SourceFragmentRepository
from steward.sources import Source, SourceType


class DocumentExtractor(Protocol):
    def extract(self, source: Source) -> ExtractionResult: ...


class DocumentExtractionError(RuntimeError):
    """A source could not be read by its format-specific extractor."""


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
        try:
            for page_number, page in enumerate(PdfReader(source.path).pages, start=1):
                text = page.extract_text().strip()
                if text:
                    fragments.append(SourceFragment(None, source.id, None, len(fragments), text, f"page {page_number}"))
        except (OSError, PyPdfError) as error:
            raise DocumentExtractionError(f"Could not read PDF {source.path}.") from error
        return ExtractionResult(source.id, tuple(fragments))


class DocxExtractor:
    """Extract non-empty Word paragraphs with their nearest heading."""

    def extract(self, source: Source) -> ExtractionResult:
        if source.id is None:
            raise ValueError("Only a persisted Source can be extracted.")
        if source.source_type is not SourceType.DOCX:
            raise ValueError("DocxExtractor requires a DOCX Source.")
        fragments = []
        current_heading: str | None = None
        try:
            for paragraph_number, paragraph in enumerate(Document(source.path).paragraphs, start=1):
                text = paragraph.text.strip()
                if not text:
                    continue
                if paragraph.style.name.casefold().startswith("heading"):
                    current_heading = text
                    continue
                fragments.append(
                    SourceFragment(
                        None,
                        source.id,
                        current_heading,
                        len(fragments),
                        text,
                        f"paragraph {paragraph_number}",
                    )
                )
        except (BadZipFile, OSError, OpcError, ValueError) as error:
            raise DocumentExtractionError(f"Could not read DOCX {source.path}.") from error
        return ExtractionResult(source.id, tuple(fragments))


class ExtractionService:
    """Choose an extractor by Source type and persist only derived fragments."""

    def __init__(self, fragment_repository: SourceFragmentRepository) -> None:
        self._fragment_repository = fragment_repository
        self._extractors: dict[SourceType, DocumentExtractor] = {
            SourceType.MARKDOWN: MarkdownExtractor(),
            SourceType.PLAIN_TEXT: PlainTextExtractor(),
            SourceType.PDF: PdfExtractor(),
            SourceType.DOCX: DocxExtractor(),
        }

    def extract_and_store(self, source: Source) -> ExtractionResult | None:
        extractor = self._extractors.get(source.source_type)
        if extractor is None:
            return None
        result = extractor.extract(source)
        self._fragment_repository.replace_for_source(result)
        return result
