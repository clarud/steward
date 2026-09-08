"""Source-aware lexical retrieval over SQLite FTS5 fragments."""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass

from steward.extraction import SourceFragment, SourceFragmentRepository
from steward.sources import Source, SourceRepository
from steward.sources.models import SourceType


@dataclass(frozen=True, slots=True)
class LexicalSearchHit:
    """A ranked fragment together with its original Source."""

    source: Source
    fragment: SourceFragment
    score: float
    highlighted_text: str | None = None


class LexicalSearchService:
    """Find locally indexed fragments with exact-term lexical matching."""

    def __init__(
        self,
        source_repository: SourceRepository,
        fragment_repository: SourceFragmentRepository,
    ) -> None:
        self._source_repository = source_repository
        self._fragment_repository = fragment_repository

    def search(
        self,
        query: str,
        *,
        limit: int = 5,
        source_types: Collection[SourceType] | None = None,
    ) -> tuple[LexicalSearchHit, ...]:
        """Search fragment text and attach each hit's original Source."""
        hits: list[LexicalSearchHit] = []
        for result in self._fragment_repository.search(
            query, limit=limit, source_types=source_types
        ):
            source = self._source_repository.get_by_id(result.fragment.source_id)
            if source is None:
                raise RuntimeError(
                    f"Fragment {result.fragment.id} refers to missing Source "
                    f"{result.fragment.source_id}."
                )
            hits.append(
                LexicalSearchHit(
                    source=source,
                    fragment=result.fragment,
                    score=result.score,
                    highlighted_text=result.highlighted_text,
                )
            )

        return tuple(hits)
