"""A minimal localhost-only browser UI over Steward's lexical retrieval."""

from __future__ import annotations

from dataclasses import dataclass
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Protocol
from urllib.parse import parse_qs, urlparse

from steward.extraction import InvalidSearchQueryError, SourceFragment, SourceFragmentRepository
from steward.sources import Source, SourceRepository


class LocalSearchService(Protocol):
    """The common query surface shared by lexical and hybrid local retrieval."""

    def search(self, query: str, *, limit: int = 5) -> tuple[object, ...]: ...


@dataclass(frozen=True, slots=True)
class SourceDetails:
    """Registered source metadata plus its already-derived local fragments."""

    source: Source
    fragments: tuple[SourceFragment, ...]


class LocalSourceBrowser:
    """Read only registered sources through derived SQLite content, never paths."""

    def __init__(
        self, source_repository: SourceRepository, fragment_repository: SourceFragmentRepository
    ) -> None:
        self._source_repository = source_repository
        self._fragment_repository = fragment_repository

    def get(self, source_id: int) -> SourceDetails | None:
        """Return a source only when its positive SQLite identity is registered."""
        if source_id <= 0:
            return None
        source = self._source_repository.get_by_id(source_id)
        if source is None:
            return None
        return SourceDetails(source, self._fragment_repository.list_for_source(source_id))


def render_search_page(
    query: str, hits: tuple[object, ...] = (), error: str | None = None, *, mode: str = "lexical"
) -> str:
    """Render untrusted source text safely into a deliberately small UI."""
    rows = []
    for hit in hits:
        source = hit.source  # type: ignore[attr-defined]
        fragment = hit.fragment  # type: ignore[attr-defined]
        excerpt = getattr(hit, "highlighted_text", None) or fragment.text  # type: ignore[attr-defined]
        rows.append(
            "<article><h2><a href='/sources/" + str(source.id) + "'>" + escape(source.path.name) + "</a></h2>"
            + "<p class='meta'>" + escape(str(source.path)) + " · "
            + escape(fragment.location) + " · " + escape(fragment.heading or "Preamble") + "</p>"
            + "<pre>" + _highlight_excerpt(excerpt) + "</pre></article>"
        )
    result_html = "".join(rows) or ("<p>No matching local fragments.</p>" if query else "")
    error_html = f"<p class='error'>{escape(error)}</p>" if error else ""
    return f"""<!doctype html><html lang='en'><head><meta charset='utf-8'>
<meta name='viewport' content='width=device-width, initial-scale=1'>
<title>Steward local search</title><style>
body{{font:16px system-ui,sans-serif;max-width:900px;margin:3rem auto;padding:0 1rem;color:#18212f}}
input{{width:min(650px,80%);padding:.65rem}}button{{padding:.65rem 1rem}}article{{border-top:1px solid #d7dce3;padding:1rem 0}}
h1{{margin-bottom:.25rem}}.meta{{color:#5d6978;font-size:.9rem}}pre{{white-space:pre-wrap;font:inherit}}.error{{color:#a21d1d}}
</style></head><body><h1>Steward</h1><p>Local {escape(mode)} search. No source text leaves this machine.</p>
<form method='get'><input name='q' value='{escape(query, quote=True)}' autofocus placeholder='Search your local sources'> <button>Search</button></form>
{error_html}<section>{result_html}</section></body></html>"""


def render_source_page(
    details: SourceDetails, *, offset: int = 0, page_size: int = 50
) -> str:
    """Render a bounded page of one source's persisted, extracted text."""
    if offset < 0:
        raise ValueError("Source fragment offset must not be negative.")
    if page_size <= 0:
        raise ValueError("Source fragment page size must be positive.")
    source = details.source
    shown = details.fragments[offset : offset + page_size]
    fragments = "".join(
        "<article><h2>"
        + escape(fragment.heading or "Preamble")
        + "</h2><p class='meta'>"
        + escape(fragment.location)
        + " · fragment "
        + str(fragment.ordinal + 1)
        + "</p><pre>"
        + escape(fragment.text)
        + "</pre></article>"
        for fragment in shown
    ) or "<p>This source has no extracted text yet.</p>"
    previous = (
        f"<a href='/sources/{source.id}?from={max(0, offset - page_size)}'>Previous fragments</a>"
        if offset
        else ""
    )
    next_offset = offset + page_size
    following = (
        f"<a href='/sources/{source.id}?from={next_offset}'>More fragments</a>"
        if next_offset < len(details.fragments)
        else ""
    )
    navigation = " · ".join(part for part in (previous, following) if part)
    return f"""<!doctype html><html lang='en'><head><meta charset='utf-8'>
<meta name='viewport' content='width=device-width, initial-scale=1'>
<title>{escape(source.path.name)} · Steward</title><style>
body{{font:16px system-ui,sans-serif;max-width:900px;margin:3rem auto;padding:0 1rem;color:#18212f}}
article{{border-top:1px solid #d7dce3;padding:1rem 0}}h1{{margin-bottom:.25rem}}
.meta{{color:#5d6978;font-size:.9rem}}pre{{white-space:pre-wrap;font:inherit}}a{{color:#0a5ea8}}
</style></head><body><p><a href='/'>← Search</a></p><h1>{escape(source.path.name)}</h1>
<p class='meta'>{escape(str(source.path))} · {escape(source.source_type.value)} · {source.size_bytes} bytes · {escape(source.status.value)}</p>
<p>Showing fragments {offset + 1 if shown else 0}–{offset + len(shown)} of {len(details.fragments)}. This is locally stored extracted text; the original file remains authoritative.</p>
<nav>{navigation}</nav><section>{fragments}</section><nav>{navigation}</nav></body></html>"""


def _highlight_excerpt(text: str) -> str:
    """Escape source text first, then render only FTS5's own marker characters."""
    return escape(text).replace("[", "<mark>").replace("]", "</mark>")


def run_local_ui(
    service: LocalSearchService,
    source_browser: LocalSourceBrowser,
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
    mode: str = "lexical",
) -> None:
    """Serve the search UI until interrupted; never bind it beyond loopback."""
    if host not in {"127.0.0.1", "::1", "localhost"}:
        raise ValueError("The local UI may bind only to a loopback host.")
    if not 1 <= port <= 65535:
        raise ValueError("UI port must be between 1 and 65535.")
    if mode not in {"lexical", "hybrid"}:
        raise ValueError("UI mode must be lexical or hybrid.")

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - stdlib handler API
            parsed = urlparse(self.path)
            if parsed.path == "/":
                query = parse_qs(parsed.query).get("q", [""])[0]
                try:
                    hits = service.search(query, limit=20) if query.strip() else ()
                    page = render_search_page(query, hits, mode=mode)
                except InvalidSearchQueryError:
                    page = render_search_page(
                        query, error="Use simple search terms; this query is not valid for the local index.", mode=mode
                    )
            elif parsed.path.startswith("/sources/"):
                source_id_text = parsed.path.removeprefix("/sources/")
                try:
                    source_id = int(source_id_text)
                    offset = int(parse_qs(parsed.query).get("from", ["0"])[0])
                except ValueError:
                    self.send_error(404)
                    return
                details = source_browser.get(source_id)
                if details is None or offset < 0:
                    self.send_error(404)
                    return
                page = render_source_page(details, offset=offset)
            else:
                self.send_error(404)
                return
            encoded = page.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def log_message(self, _format: str, *args: object) -> None:
            return

    server = ThreadingHTTPServer((host, port), Handler)
    print(f"Steward local {mode} UI running at http://{host}:{port} (Ctrl+C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
