"""Fusion of lexical and semantic evidence retrieval."""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass

from steward.extraction import InvalidSearchQueryError, SourceFragment
from steward.retrieval.lexical import LexicalSearchService
from steward.retrieval.semantic import SemanticSearchService
from steward.sources import Source
from steward.sources.models import SourceType

RRF_K = 60


@dataclass(frozen=True, slots=True)
class HybridSearchHit:
    """A source fragment ranked by reciprocal-rank fusion."""

    source: Source
    fragment: SourceFragment
    score: float
    lexical_score: float | None
    semantic_score: float | None


class HybridRetriever:
    """Combine rankings without assuming their numerical scores are comparable."""

    def __init__(
        self,
        lexical_search: LexicalSearchService,
        semantic_search: SemanticSearchService,
    ) -> None:
        self._lexical_search = lexical_search
        self._semantic_search = semantic_search

    def search(
        self,
        query: str,
        *,
        limit: int = 5,
        source_types: Collection[SourceType] | None = None,
    ) -> tuple[HybridSearchHit, ...]:
        """Fuse lexical and semantic rankings using reciprocal-rank fusion."""
        if limit <= 0:
            raise ValueError("Search limit must be positive.")
        candidate_limit = limit * 3
        try:
            lexical_hits = self._lexical_search.search(
                query, limit=candidate_limit, source_types=source_types
            )
        except InvalidSearchQueryError:
            # Natural-language punctuation may be invalid FTS5 syntax. Semantic
            # search can still retrieve useful evidence for the same question.
            lexical_hits = ()
        semantic_hits = self._semantic_search.search(
            query, limit=candidate_limit, source_types=source_types
        )

        combined: dict[int, HybridSearchHit] = {}
        for rank, hit in enumerate(lexical_hits, start=1):
            if hit.fragment.id is None:
                raise RuntimeError("A persisted lexical fragment must have an ID.")
            combined[hit.fragment.id] = HybridSearchHit(
                source=hit.source,
                fragment=hit.fragment,
                score=1 / (RRF_K + rank),
                lexical_score=hit.score,
                semantic_score=None,
            )
        for rank, hit in enumerate(semantic_hits, start=1):
            if hit.fragment.id is None:
                raise RuntimeError("A persisted semantic fragment must have an ID.")
            existing = combined.get(hit.fragment.id)
            semantic_contribution = 1 / (RRF_K + rank)
            if existing is None:
                combined[hit.fragment.id] = HybridSearchHit(
                    source=hit.source,
                    fragment=hit.fragment,
                    score=semantic_contribution,
                    lexical_score=None,
                    semantic_score=hit.score,
                )
            else:
                combined[hit.fragment.id] = HybridSearchHit(
                    source=existing.source,
                    fragment=existing.fragment,
                    score=existing.score + semantic_contribution,
                    lexical_score=existing.lexical_score,
                    semantic_score=hit.score,
                )

        return tuple(
            sorted(combined.values(), key=lambda hit: hit.score, reverse=True)[:limit]
        )
