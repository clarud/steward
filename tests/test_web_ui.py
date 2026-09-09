from datetime import UTC, datetime
from pathlib import Path

import pytest

from steward.extraction import SourceFragment
from steward.sources import Source, SourceStatus, SourceType
from steward.web_ui import SourceDetails, _highlight_excerpt, render_records_page, render_search_page, render_source_page, run_local_ui


def test_search_page_escapes_untrusted_query_and_source_content() -> None:
    page = render_search_page("<script>", error="Bad <query>")

    assert "&lt;script&gt;" in page
    assert "Bad &lt;query&gt;" in page
    assert "<script>" not in page


def test_local_ui_refuses_non_loopback_hosts() -> None:
    with pytest.raises(ValueError, match="loopback"):
        run_local_ui(None, None, None, host="0.0.0.0")  # type: ignore[arg-type]


def test_local_ui_rejects_unknown_retrieval_modes() -> None:
    with pytest.raises(ValueError, match="mode"):
        run_local_ui(None, None, None, mode="cloud")  # type: ignore[arg-type]


def test_excerpt_highlighting_escapes_source_text_before_markup() -> None:
    assert _highlight_excerpt("<img [TLB]>") == "&lt;img <mark>TLB</mark>&gt;"


def test_source_page_escapes_derived_text_and_paginates() -> None:
    now = datetime.now(UTC)
    source = Source(1, Path("<notes>.md"), "a" * 64, SourceType.MARKDOWN, 12, now, now, now, SourceStatus.ACTIVE)
    fragments = tuple(
        SourceFragment(index + 1, 1, "<heading>", index, f"<text {index}>", f"section {index}")
        for index in range(51)
    )

    page = render_source_page(SourceDetails(source, fragments), page_size=50)

    assert "&lt;notes&gt;.md" in page
    assert "&lt;text 0&gt;" in page
    assert "<text 0>" not in page
    assert "More fragments" in page
    assert "Showing fragments 1–50 of 51" in page


def test_records_page_escapes_summaries_and_links_to_registered_source() -> None:
    page = render_records_page(({"type": "Receipt", "id": 1, "source_id": 2, "summary": "<store>"},))
    assert "&lt;store&gt;" in page
    assert "<store>" not in page
    assert "href='/sources/2'" in page
