"""File-type strategies for deterministic document text extraction."""

from __future__ import annotations

from html.parser import HTMLParser
import json
from email import policy
from email.parser import BytesParser
from pathlib import Path
from subprocess import CalledProcessError, TimeoutExpired, run
from tempfile import TemporaryDirectory
from typing import Protocol
from zipfile import BadZipFile
from zipfile import ZipFile
from xml.etree import ElementTree

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


class CodeExtractor:
    """Chunk source code by bounded line ranges while preserving exact locations."""

    MAX_LINES = 120

    def extract(self, source: Source) -> ExtractionResult:
        if source.id is None:
            raise ValueError("Only a persisted Source can be extracted.")
        if source.source_type is not SourceType.CODE:
            raise ValueError("CodeExtractor requires a code Source.")
        lines = source.path.read_text(encoding="utf-8").splitlines()
        fragments = []
        for start in range(0, len(lines), self.MAX_LINES):
            chunk = "\n".join(lines[start:start + self.MAX_LINES]).strip()
            if not chunk:
                continue
            end = min(start + self.MAX_LINES, len(lines))
            fragments.append(SourceFragment(None, source.id, None, len(fragments), chunk, f"lines {start + 1}-{end}"))
        return ExtractionResult(source.id, tuple(fragments))


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


class PdfOcrExtractor:
    """Rasterize a scanned PDF locally, then OCR each page with Tesseract."""

    def __init__(self, *, renderer_command: str = "pdftoppm", ocr_command: str = "tesseract") -> None:
        self._renderer_command = renderer_command
        self._ocr_command = ocr_command

    def extract(self, source: Source) -> ExtractionResult:
        if source.id is None:
            raise ValueError("Only a persisted Source can be extracted.")
        if source.source_type is not SourceType.PDF:
            raise ValueError("PdfOcrExtractor requires a PDF Source.")
        try:
            with TemporaryDirectory(prefix="steward-pdf-ocr-") as temporary_directory:
                image_prefix = Path(temporary_directory) / "page"
                run(
                    [self._renderer_command, "-png", "-r", "200", str(source.path), str(image_prefix)],
                    capture_output=True,
                    check=True,
                    encoding="utf-8",
                    text=True,
                    timeout=120,
                )
                images = sorted(Path(temporary_directory).glob("page-*.png"))
                if not images:
                    raise DocumentExtractionError(f"Could not rasterize any pages from PDF {source.path}.")
                fragments = []
                for page_number, image_path in enumerate(images, start=1):
                    completed = run(
                        [self._ocr_command, str(image_path), "stdout"],
                        capture_output=True,
                        check=True,
                        encoding="utf-8",
                        text=True,
                        timeout=60,
                    )
                    text = completed.stdout.strip()
                    if text:
                        fragments.append(
                            SourceFragment(None, source.id, None, len(fragments), text, f"page {page_number} OCR")
                        )
        except (OSError, CalledProcessError, TimeoutExpired) as error:
            raise DocumentExtractionError(
                f"Could not OCR scanned PDF {source.path}; install local Poppler and Tesseract to enable it."
            ) from error
        return ExtractionResult(source.id, tuple(fragments))


class PdfExtractor:
    """Extract native PDF text first, then use optional local OCR for image-only PDFs."""

    def __init__(self, ocr_extractor: PdfOcrExtractor | None = None) -> None:
        self._ocr_extractor = ocr_extractor or PdfOcrExtractor()

    def extract(self, source: Source) -> ExtractionResult:
        if source.id is None:
            raise ValueError("Only a persisted Source can be extracted.")
        if source.source_type is not SourceType.PDF:
            raise ValueError("PdfExtractor requires a PDF Source.")
        fragments = []
        try:
            for page_number, page in enumerate(PdfReader(source.path).pages, start=1):
                text = (page.extract_text() or "").strip()
                if text:
                    fragments.append(SourceFragment(None, source.id, None, len(fragments), text, f"page {page_number}"))
        except (OSError, PyPdfError) as error:
            raise DocumentExtractionError(f"Could not read PDF {source.path}.") from error
        if not fragments:
            return self._ocr_extractor.extract(source)
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


class PptxExtractor:
    """Extract visible slide text from an OOXML presentation without a model."""

    _TEXT = "{http://schemas.openxmlformats.org/drawingml/2006/main}t"

    def extract(self, source: Source) -> ExtractionResult:
        if source.id is None:
            raise ValueError("Only a persisted Source can be extracted.")
        if source.source_type is not SourceType.PPTX:
            raise ValueError("PptxExtractor requires a PPTX Source.")
        try:
            with ZipFile(source.path) as archive:
                slides = sorted(
                    (name for name in archive.namelist() if name.startswith("ppt/slides/slide") and name.endswith(".xml")),
                    key=lambda name: int(name.rsplit("slide", 1)[1].removesuffix(".xml")),
                )
                fragments = []
                for index, name in enumerate(slides, start=1):
                    root = ElementTree.fromstring(archive.read(name))
                    text = " ".join(part.text or "" for part in root.iter(self._TEXT)).strip()
                    if text:
                        fragments.append(SourceFragment(None, source.id, None, len(fragments), text, f"slide {index}"))
        except (BadZipFile, OSError, ElementTree.ParseError, KeyError, ValueError) as error:
            raise DocumentExtractionError(f"Could not read PPTX {source.path}.") from error
        return ExtractionResult(source.id, tuple(fragments))


class XlsxExtractor:
    """Extract non-empty worksheet rows with sheet/cell-range provenance."""

    _MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    _REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"

    def extract(self, source: Source) -> ExtractionResult:
        if source.id is None:
            raise ValueError("Only a persisted Source can be extracted.")
        if source.source_type is not SourceType.XLSX:
            raise ValueError("XlsxExtractor requires an XLSX Source.")
        try:
            with ZipFile(source.path) as archive:
                shared = self._shared_strings(archive)
                workbook = ElementTree.fromstring(archive.read("xl/workbook.xml"))
                relationships = ElementTree.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
                targets = {item.attrib.get("Id"): item.attrib.get("Target") for item in relationships}
                fragments = []
                for sheet in workbook.findall(f".//{{{self._MAIN}}}sheet"):
                    relationship_id = sheet.attrib.get(f"{{{self._REL}}}id")
                    target = targets.get(relationship_id)
                    if not target:
                        continue
                    worksheet = ElementTree.fromstring(archive.read("xl/" + target.lstrip("/")))
                    sheet_name = sheet.attrib.get("name", "Sheet")
                    for row in worksheet.findall(f".//{{{self._MAIN}}}row"):
                        values = []
                        cells = row.findall(f"{{{self._MAIN}}}c")
                        for cell in cells:
                            value = cell.find(f"{{{self._MAIN}}}v")
                            if value is None or value.text is None:
                                continue
                            raw = value.text
                            values.append(shared[int(raw)] if cell.attrib.get("t") == "s" else raw)
                        if values:
                            row_number = row.attrib.get("r", "?")
                            fragments.append(SourceFragment(None, source.id, sheet_name, len(fragments), " | ".join(values), f"{sheet_name}!row {row_number}"))
        except (BadZipFile, OSError, ElementTree.ParseError, KeyError, ValueError, IndexError) as error:
            raise DocumentExtractionError(f"Could not read XLSX {source.path}.") from error
        return ExtractionResult(source.id, tuple(fragments))

    def _shared_strings(self, archive: ZipFile) -> list[str]:
        try:
            root = ElementTree.fromstring(archive.read("xl/sharedStrings.xml"))
        except KeyError:
            return []
        return ["".join(part.text or "" for part in item.iter(f"{{{self._MAIN}}}t")) for item in root.findall(f"{{{self._MAIN}}}si")]


class NotebookExtractor:
    """Extract readable Jupyter markdown and code cells with cell provenance."""

    def extract(self, source: Source) -> ExtractionResult:
        if source.id is None:
            raise ValueError("Only a persisted Source can be extracted.")
        if source.source_type is not SourceType.NOTEBOOK:
            raise ValueError("NotebookExtractor requires an .ipynb Source.")
        try:
            notebook = json.loads(source.path.read_text(encoding="utf-8"))
            cells = notebook["cells"]
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError) as error:
            raise DocumentExtractionError(f"Could not read notebook {source.path}.") from error
        fragments = []
        for cell_number, cell in enumerate(cells, start=1):
            if not isinstance(cell, dict) or cell.get("cell_type") not in {"markdown", "code"}:
                continue
            raw = cell.get("source", [])
            text = "".join(raw) if isinstance(raw, list) else raw if isinstance(raw, str) else ""
            if text.strip():
                fragments.append(SourceFragment(None, source.id, cell.get("cell_type"), len(fragments), text.strip(), f"cell {cell_number}"))
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
            SourceType.PPTX: PptxExtractor(),
            SourceType.XLSX: XlsxExtractor(),
            SourceType.NOTEBOOK: NotebookExtractor(),
            SourceType.HTML: HtmlExtractor(),
            SourceType.IMAGE: ImageOcrExtractor(),
            SourceType.CODE: CodeExtractor(),
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
