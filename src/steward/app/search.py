"""/find: locate files by words or meaning, scoped by root and type."""

from __future__ import annotations

import shlex
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from steward.extraction import InvalidSearchQueryError
from steward.presentation import PresentedReply, ReplyAction
from steward.retrieval import HybridRetriever, LexicalSearchService
from steward.roots import SourceRootRepository
from steward.sources import Source, SourceRepository, SourceType


@dataclass(frozen=True, slots=True)
class SearchScope:
    query: str
    source_types: tuple[SourceType, ...] = ()
    path_prefix: Path | None = None
    label: str = ""

    def kwargs(self, limit: int) -> dict[str, object]:
        options: dict[str, object] = {"limit": limit}
        if self.source_types:
            options["source_types"] = self.source_types
        if self.path_prefix is not None:
            options["path_prefix"] = self.path_prefix
        return options


def parse_scope(raw: str, roots: SourceRootRepository) -> SearchScope | str:
    """Parse `words [--type TYPE] [--root "NAME"]`; roots resolve only by name."""
    try:
        tokens = shlex.split(raw)
    except ValueError:
        return "Your search has an unmatched quote. Close it and try again."
    terms: list[str] = []
    types: list[SourceType] = []
    root_name: str | None = None
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token in {"--type", "--root"}:
            index += 1
            if index == len(tokens):
                return f"Add a value after {token}, for example --type pdf or --root \"Y4S1\"."
            if token == "--type":
                try:
                    types.append(SourceType(tokens[index].casefold()))
                except ValueError:
                    return "Unknown type. Try markdown, plain_text, pdf, docx, pptx, xlsx, notebook, html, image, or code."
            else:
                root_name = tokens[index]
        else:
            terms.append(token)
        index += 1
    if not terms:
        return "Add some words to look for, for example /find queueing notes --type pdf."
    labels = []
    path_prefix = None
    if root_name is not None:
        root = next((item for item in roots.list_all() if item.name.casefold() == root_name.casefold()), None)
        if root is None:
            return f"No folder is named {root_name!r}. Open /sources to see your folders."
        path_prefix = root.path
        labels.append(f"in {root.name}")
    source_types = tuple(dict.fromkeys(types))
    if source_types:
        labels.append("type " + ", ".join(item.value for item in source_types))
    query = " ".join(terms)
    return SearchScope(query, source_types, path_prefix, query + (f" ({'; '.join(labels)})" if labels else ""))


class StewardSearchApplication:
    """Plain retrieval behind /find; no model call."""

    def __init__(
        self,
        sources: SourceRepository,
        lexical: LexicalSearchService,
        roots: SourceRootRepository,
        *,
        hybrid: HybridRetriever | None = None,
        location: Callable[[Source], str] | None = None,
    ) -> None:
        self._sources = sources
        self._lexical = lexical
        self._roots = roots
        self._hybrid = hybrid
        self._location = location

    def find(self, raw: str) -> PresentedReply | str:
        scope = parse_scope(raw, self._roots)
        if isinstance(scope, str):
            return scope
        hits = ()
        if self._hybrid is not None:
            try:
                hits = self._hybrid.search(scope.query, **scope.kwargs(5))
            except (OSError, RuntimeError, ValueError):
                hits = ()
        if not hits:
            try:
                hits = self._lexical.search(scope.query, **scope.kwargs(5))
            except InvalidSearchQueryError:
                hits = ()
        if hits:
            return self._results_card(scope, hits)
        filenames = self._sources.search_filenames(
            scope.query, limit=5, source_types=scope.source_types, path_prefix=scope.path_prefix,
        )
        if filenames:
            return self._filename_card(scope, filenames)
        return f"Nothing matched {scope.label!r}. Try other words, or /ask a question."

    def _results_card(self, scope: SearchScope, hits: object) -> PresentedReply:
        lines = [f"Results for {scope.label}"]
        actions = []
        for index, hit in enumerate(hits, start=1):  # type: ignore[union-attr]
            excerpt = (getattr(hit, "highlighted_text", None) or hit.fragment.text).replace("\n", " ").strip()
            lines.append(
                f"\n{index}. {hit.source.path.name}\n{self._where(hit.source)} · {hit.fragment.location}\n“{excerpt[:180]}”"
            )
            if hit.source.id is not None:
                actions.append(ReplyAction(f"Open {index}", f"/source {hit.source.id}"))
        return PresentedReply("\n".join(lines), tuple(actions), title="Search results", icon="🔎")

    def _filename_card(self, scope: SearchScope, sources: tuple[Source, ...]) -> PresentedReply:
        lines = [f"No text matched. Files whose name or folder matches {scope.label}:"]
        actions = []
        for index, source in enumerate(sources, start=1):
            lines.append(f"\n{index}. {source.path.name}\n{self._where(source)} · {source.source_type.value}")
            if source.id is not None:
                actions.append(ReplyAction(f"Open {index}", f"/source {source.id}"))
        return PresentedReply("\n".join(lines), tuple(actions), title="Filename matches", icon="🔎")

    def _where(self, source: Source) -> str:
        return self._location(source) if self._location is not None else source.path.name
