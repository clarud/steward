"""File-type strategies for deterministic document text extraction."""

from __future__ import annotations

from html.parser import HTMLParser
from email import policy
from email.parser import BytesParser
from subprocess import CalledProcessError, TimeoutExpired, run
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


class _HeadingHtmlParser(HTMLParser):
    """Collect visible text into sections, retaining the nearest HTML heading."""

    _HEADING_TAGS = frozenset({"h1", "h2", "h3", "h4", "h5", "h6"})
    _IGNORED_TAGS = frozenset({"script", "style", "template", "noscript"})

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._current_heading: str | None = None
        self._heading_parts: list[str] | None = None
        self._text_parts: list[str] = []
        self._ignored_depth = 0
        self.fragments: list[tuple[str | None, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        normalized_tag = tag.casefold()
        if normalized_tag in self._IGNORED_TAGS:
            self._ignored_depth += 1
            return
        if self._ignored_depth:
            return
        if normalized_tag in self._HEADING_TAGS:
            self._flush_text()
            self._heading_parts = []
        elif normalized_tag in {"br", "p", "li", "div", "section", "article"}:
            self._text_parts.append(" ")

    def handle_endtag(self, tag: str) -> None:
        normalized_tag = tag.casefold()
        if normalized_tag in self._IGNORED_TAGS:
            self._ignored_depth = max(0, self._ignored_depth - 1)
            return
        if self._ignored_depth:
            return
        if normalized_tag in self._HEADING_TAGS and self._heading_parts is not None:
            heading = self._normalize(self._heading_parts)
            if heading:
                self._current_heading = heading
            self._heading_parts = None
        elif normalized_tag in {"p", "li", "div", "section", "article"}:
            self._text_parts.append(" ")

    def handle_data(self, data: str) -> None:
        if self._ignored_depth:
            return
        if self._heading_parts is not None:
            self._heading_parts.append(data)
        else:
            self._text_parts.append(data)

    def finish(self) -> tuple[tuple[str | None, str], ...]:
        self._flush_text()
        return tuple(self.fragments)

    def _flush_text(self) -> None:
        text = self._normalize(self._text_parts)
        if text:
            self.fragments.append((self._current_heading, text))
        self._text_parts = []

    @staticmethod
    def _normalize(parts: list[str]) -> str:
        return " ".join("".join(parts).split())


class PlainTextExtractor:
    def extract(self, source: Source) -> ExtractionResult:
        if source.id is None:
            raise ValueError("Only a persisted Source can be extracted.")
        text = source.path.read_text(encoding="utf-8").strip()
        fragments = () if not text else (SourceFragment(None, source.id, None, 0, text, "entire file"),)
        return ExtractionResult(source.id, fragments)


class EmailExtractor:
    """Extract readable non-attachment message parts from a raw RFC 822 original."""

    def extract(self, source: Source) -> ExtractionResult:
        if source.id is None:
            raise ValueError("Only a persisted Source can be extracted.")
        if source.path.suffix.casefold() != ".eml":
            raise ValueError("EmailExtractor requires an .eml Source.")
        try:
            message = BytesParser(policy=policy.default).parsebytes(source.path.read_bytes())
        except (OSError, ValueError) as error:
            raise DocumentExtractionError(f"Could not read email {source.path}.") from error
        subject = str(message.get("subject") or "(no subject)").strip()
        fragments = []
        text_part_number = 0
        for part in message.walk():
            if part.is_multipart() or part.get_content_disposition() == "attachment":
                continue
            content_type = part.get_content_type()
            if content_type not in {"text/plain", "text/html"}:
                continue
            try:
                text = part.get_content()
            except (LookupError, UnicodeError, ValueError) as error:
                raise DocumentExtractionError(f"Could not decode email {source.path}.") from error
            if not isinstance(text, str):
                continue
            if content_type == "text/html":
                parser = _HeadingHtmlParser()
                parser.feed(text)
                parser.close()
                text = "\n\n".join(section for _, section in parser.finish())
            normalized = text.strip()
            if not normalized:
                continue
            text_part_number += 1
            fragments.append(
                SourceFragment(
                    None, source.id, subject, len(fragments), normalized,
                    f"email text part {text_part_number}",
                )
            )
        return ExtractionResult(source.id, tuple(fragments))


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


class HtmlExtractor:
    """Extract visible HTML text into heading-delimited fragments."""

    def extract(self, source: Source) -> ExtractionResult:
        if source.id is None:
            raise ValueError("Only a persisted Source can be extracted.")
        if source.source_type is not SourceType.HTML:
            raise ValueError("HtmlExtractor requires an HTML Source.")
        parser = _HeadingHtmlParser()
        parser.feed(source.path.read_text(encoding="utf-8"))
        parser.close()
        fragments = tuple(
            SourceFragment(
                None,
                source.id,
                heading,
                ordinal,
                text,
                f"section {ordinal + 1}",
            )
            for ordinal, (heading, text) in enumerate(parser.finish())
        )
        return ExtractionResult(source.id, fragments)


class ImageOcrExtractor:
    """Use the optional local Tesseract executable to extract image text."""

    def __init__(self, command: str = "tesseract") -> None:
        self._command = command

    def extract(self, source: Source) -> ExtractionResult:
        if source.id is None:
            raise ValueError("Only a persisted Source can be extracted.")
        if source.source_type is not SourceType.IMAGE:
            raise ValueError("ImageOcrExtractor requires an image Source.")
        try:
            completed = run(
                [self._command, str(source.path), "stdout"],
                capture_output=True,
                check=True,
                encoding="utf-8",
                text=True,
                timeout=30,
            )
        except (OSError, CalledProcessError, TimeoutExpired) as error:
            raise DocumentExtractionError(f"Could not OCR image {source.path}.") from error
        text = completed.stdout.strip()
        fragments = () if not text else (SourceFragment(None, source.id, None, 0, text, "image OCR"),)
        return ExtractionResult(source.id, fragments)


class ExtractionService:
    """Choose an extractor by Source type and persist only derived fragments."""

    def __init__(self, fragment_repository: SourceFragmentRepository) -> None:
        self._fragment_repository = fragment_repository
        self._extractors: dict[SourceType, DocumentExtractor] = {
            SourceType.MARKDOWN: MarkdownExtractor(),
            SourceType.PLAIN_TEXT: PlainTextExtractor(),
            SourceType.PDF: PdfExtractor(),
            SourceType.DOCX: DocxExtractor(),
            SourceType.HTML: HtmlExtractor(),
            SourceType.IMAGE: ImageOcrExtractor(),
        }

    def extract_and_store(self, source: Source) -> ExtractionResult | None:
        extractor: DocumentExtractor | None = (
            EmailExtractor() if source.path.suffix.casefold() == ".eml" else self._extractors.get(source.source_type)
        )
        if extractor is None:
            return None
        result = extractor.extract(source)
        self._fragment_repository.replace_for_source(result)
        return result
