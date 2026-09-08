import pytest

from steward.web_ui import _highlight_excerpt, render_search_page, run_local_ui


def test_search_page_escapes_untrusted_query_and_source_content() -> None:
    page = render_search_page("<script>", error="Bad <query>")

    assert "&lt;script&gt;" in page
    assert "Bad &lt;query&gt;" in page
    assert "<script>" not in page


def test_local_ui_refuses_non_loopback_hosts() -> None:
    with pytest.raises(ValueError, match="loopback"):
        run_local_ui(None, host="0.0.0.0")  # type: ignore[arg-type]


def test_excerpt_highlighting_escapes_source_text_before_markup() -> None:
    assert _highlight_excerpt("<img [TLB]>") == "&lt;img <mark>TLB</mark>&gt;"
