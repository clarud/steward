"""A minimal localhost-only browser UI over Steward's lexical retrieval."""

from __future__ import annotations

from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Protocol
from urllib.parse import parse_qs, urlparse

from steward.extraction import InvalidSearchQueryError


class LocalSearchService(Protocol):
    """The common query surface shared by lexical and hybrid local retrieval."""

    def search(self, query: str, *, limit: int = 5) -> tuple[object, ...]: ...


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
            "<article><h2>" + escape(source.path.name) + "</h2>"
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


def _highlight_excerpt(text: str) -> str:
    """Escape source text first, then render only FTS5's own marker characters."""
    return escape(text).replace("[", "<mark>").replace("]", "</mark>")


def run_local_ui(
    service: LocalSearchService,
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
            if parsed.path != "/":
                self.send_error(404)
                return
            query = parse_qs(parsed.query).get("q", [""])[0]
            try:
                hits = service.search(query, limit=20) if query.strip() else ()
                page = render_search_page(query, hits, mode=mode)
            except InvalidSearchQueryError:
                page = render_search_page(
                    query, error="Use simple search terms; this query is not valid for the local index.", mode=mode
                )
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
