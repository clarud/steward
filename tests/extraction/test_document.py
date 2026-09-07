from datetime import UTC, datetime
from pathlib import Path

from steward.extraction import PdfExtractor, PlainTextExtractor
from steward.sources import Source, SourceType


def test_plain_text_extractor_keeps_entire_file_provenance(tmp_path: Path) -> None:
    path = tmp_path / "note.txt"
    path.write_text("A TLB caches translations.", encoding="utf-8")
    source = Source(1, path, "a" * 64, SourceType.PLAIN_TEXT, path.stat().st_size,
                    datetime(2026, 9, 8, tzinfo=UTC), datetime(2026, 9, 8, tzinfo=UTC), datetime(2026, 9, 8, tzinfo=UTC))

    result = PlainTextExtractor().extract(source)

    assert result.fragments[0].location == "entire file"
    assert result.fragments[0].text == "A TLB caches translations."


def test_pdf_extractor_keeps_page_provenance(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "note.pdf"
    path.write_bytes(b"%PDF")
    source = Source(1, path, "a" * 64, SourceType.PDF, path.stat().st_size,
                    datetime(2026, 9, 8, tzinfo=UTC), datetime(2026, 9, 8, tzinfo=UTC), datetime(2026, 9, 8, tzinfo=UTC))

    class Page:
        def __init__(self, text): self._text = text
        def extract_text(self): return self._text
    class Reader:
        pages = [Page("first"), Page(""), Page("third")]
    monkeypatch.setattr("steward.extraction.document.PdfReader", lambda path: Reader())

    result = PdfExtractor().extract(source)

    assert [(fragment.text, fragment.location) for fragment in result.fragments] == [
        ("first", "page 1"), ("third", "page 3")
    ]
