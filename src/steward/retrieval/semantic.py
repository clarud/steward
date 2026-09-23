"""Local semantic retrieval backed by rebuildable SQLite embeddings."""

from __future__ import annotations

import json
import math
import sqlite3
from collections.abc import Collection, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from steward.extraction import SourceFragment
from steward.extras import MissingExtraError
from steward.sources import Source, SourceRepository
from steward.sources.models import SourceStatus, SourceType


class EmbeddingProvider(Protocol):
    """Turn text into vectors without exposing a model vendor to callers."""

    @property
    def model_name(self) -> str:
        """Return a stable name for the model that produced its vectors."""

    def embed_documents(self, texts: Sequence[str]) -> tuple[tuple[float, ...], ...]:
        """Embed source text for indexing."""

    def embed_query(self, query: str) -> tuple[float, ...]:
        """Embed a search query in the same vector space as source text."""


class SentenceTransformerEmbeddingProvider:
    """A local Sentence Transformers implementation of :class:`EmbeddingProvider`."""

    def __init__(
        self,
        model_name: str = "sentence-transformers/all-MiniLM-L6-v2",
        *,
        allow_download: bool = False,
    ) -> None:
        self._model_name = model_name
        # Import here so non-semantic commands do not load PyTorch at startup.
        # A normal search is offline after the user explicitly downloads a model.
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as error:
            raise MissingExtraError("semantic", "Semantic and hybrid search") from error

        self._model = SentenceTransformer(
            model_name, local_files_only=not allow_download
        )

    @property
    def model_name(self) -> str:
        return self._model_name

    def embed_documents(self, texts: Sequence[str]) -> tuple[tuple[float, ...], ...]:
        vectors = self._model.encode(
            list(texts), normalize_embeddings=True, convert_to_numpy=True
        )
        return tuple(tuple(float(value) for value in vector) for vector in vectors)

    def embed_query(self, query: str) -> tuple[float, ...]:
        return self.embed_documents((query,))[0]


@dataclass(frozen=True, slots=True)
class SemanticFragmentHit:
    """One fragment retrieved by cosine similarity."""

    fragment: SourceFragment
    score: float


class SemanticIndex(Protocol):
    """Store and search derived vectors independently from a vector backend."""

    def replace_for_source(
        self, fragments: Sequence[SourceFragment]
    ) -> tuple[SourceFragment, ...]:
        """Replace the indexed vectors for one source's current fragments."""

    def search(
        self,
        query: str,
        *,
        limit: int = 5,
        source_types: Collection[SourceType] | None = None,
        path_prefix: Path | None = None,
    ) -> tuple[SemanticFragmentHit, ...]:
        """Return the fragments most semantically similar to a query."""

    def clear(self) -> int:
        """Remove only rebuildable vectors for this embedding model."""


class SQLiteSemanticIndex:
    """A transparent local semantic index suitable for Steward's first vectors.

    It stores vectors as JSON because the representation is inspectable and easy
    to rebuild.  It scans the small local index in Python; a future vector
    backend can implement the same ``SemanticIndex`` protocol without changing
    services above this layer.
    """

    def __init__(self, database_path: Path, embedding_provider: EmbeddingProvider) -> None:
        self._database_path = database_path
        self._embedding_provider = embedding_provider

    def replace_for_source(
        self, fragments: Sequence[SourceFragment]
    ) -> tuple[SourceFragment, ...]:
        """Embed and atomically replace a source's current persisted fragments."""
        fragments = tuple(fragments)
        if not fragments:
            return fragments
        if any(fragment.id is None for fragment in fragments):
            raise ValueError("Only persisted SourceFragments can be semantically indexed.")
        source_ids = {fragment.source_id for fragment in fragments}
        if len(source_ids) != 1:
            raise ValueError("All indexed fragments must belong to one Source.")

        vectors = self._embedding_provider.embed_documents(
            tuple(fragment.text for fragment in fragments)
        )
        if len(vectors) != len(fragments):
            raise RuntimeError("Embedding provider returned the wrong number of vectors.")
        if not vectors or not vectors[0]:
            raise RuntimeError("Embedding provider returned an empty vector.")
        dimension = len(vectors[0])
        if any(len(vector) != dimension for vector in vectors):
            raise RuntimeError("Embedding provider returned inconsistent vector dimensions.")

        source_id = fragments[0].source_id
        with sqlite3.connect(self._database_path) as connection:
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute(
                """
                DELETE FROM source_fragment_embeddings
                WHERE fragment_id IN (
                    SELECT id FROM source_fragments WHERE source_id = ?
                )
                """,
                (source_id,),
            )
            connection.executemany(
                """
                INSERT INTO source_fragment_embeddings (
                    fragment_id, model_name, dimension, vector_json
                ) VALUES (?, ?, ?, ?)
                """,
                (
                    (
                        fragment.id,
                        self._embedding_provider.model_name,
                        dimension,
                        json.dumps(vector),
                    )
                    for fragment, vector in zip(fragments, vectors, strict=True)
                ),
            )
        return fragments

    def search(
        self,
        query: str,
        *,
        limit: int = 5,
        source_types: Collection[SourceType] | None = None,
        path_prefix: Path | None = None,
    ) -> tuple[SemanticFragmentHit, ...]:
        """Score every vector for this model with cosine similarity."""
        if not query.strip():
            raise ValueError("Search query must not be empty.")
        if limit <= 0:
            raise ValueError("Search limit must be positive.")

        query_vector = self._embedding_provider.embed_query(query)
        if not query_vector:
            raise RuntimeError("Embedding provider returned an empty query vector.")
        selected_source_types = tuple(sorted({source_type.value for source_type in source_types or ()}))
        source_type_filter = (
            f" AND s.source_type IN ({', '.join('?' for _ in selected_source_types)})"
            if selected_source_types
            else ""
        )
        normalized_prefix = str(path_prefix.resolve()) if path_prefix is not None else None
        path_filter = " AND s.path LIKE ?" if normalized_prefix is not None else ""
        with sqlite3.connect(self._database_path) as connection:
            rows = connection.execute(
                f"""
                SELECT sf.id, sf.source_id, sf.heading, sf.ordinal, sf.text,
                       sf.location, sfe.vector_json
                FROM source_fragment_embeddings AS sfe
                JOIN source_fragments AS sf ON sf.id = sfe.fragment_id
                JOIN sources AS s ON s.id = sf.source_id
                WHERE sfe.model_name = ? AND sfe.dimension = ?
                  AND s.status = ?
                  {source_type_filter}
                  {path_filter}
                """,
                (
                    self._embedding_provider.model_name,
                    len(query_vector),
                    SourceStatus.ACTIVE.value,
                    *selected_source_types,
                    *((normalized_prefix + "%",) if normalized_prefix is not None else ()),
                ),
            ).fetchall()

        hits = [
            SemanticFragmentHit(
                fragment=SourceFragment(
                    id=int(row[0]),
                    source_id=int(row[1]),
                    heading=str(row[2]) if row[2] is not None else None,
                    ordinal=int(row[3]),
                    text=str(row[4]),
                    location=str(row[5]),
                ),
                score=_cosine_similarity(query_vector, tuple(json.loads(str(row[6])))),
            )
            for row in rows
        ]
        return tuple(sorted(hits, key=lambda hit: hit.score, reverse=True)[:limit])

    def clear(self) -> int:
        """Remove this provider's derived vectors without touching source fragments."""

        with sqlite3.connect(self._database_path) as connection:
            cursor = connection.execute(
                "DELETE FROM source_fragment_embeddings WHERE model_name = ?",
                (self._embedding_provider.model_name,),
            )
        return cursor.rowcount


@dataclass(frozen=True, slots=True)
class SemanticSearchHit:
    """A semantic match accompanied by its authoritative Source."""

    source: Source
    fragment: SourceFragment
    score: float


class SemanticSearchService:
    """Attach original-source provenance to semantic fragment matches."""

    def __init__(self, source_repository: SourceRepository, semantic_index: SemanticIndex) -> None:
        self._source_repository = source_repository
        self._semantic_index = semantic_index

    def search(
        self,
        query: str,
        *,
        limit: int = 5,
        source_types: Collection[SourceType] | None = None,
        path_prefix: Path | None = None,
    ) -> tuple[SemanticSearchHit, ...]:
        hits: list[SemanticSearchHit] = []
        for result in self._semantic_index.search(
            query, limit=limit, source_types=source_types, path_prefix=path_prefix
        ):
            source = self._source_repository.get_by_id(result.fragment.source_id)
            if source is None:
                raise RuntimeError(
                    f"Fragment {result.fragment.id} refers to missing Source "
                    f"{result.fragment.source_id}."
                )
            hits.append(
                SemanticSearchHit(source, result.fragment, result.score)
            )
        return tuple(hits)


def _cosine_similarity(left: Sequence[float], right: Sequence[float]) -> float:
    """Return the cosine of two vectors, rejecting incompatible vectors."""
    if len(left) != len(right):
        raise RuntimeError("Stored embedding dimension does not match the query vector.")
    left_magnitude = math.sqrt(sum(value * value for value in left))
    right_magnitude = math.sqrt(sum(value * value for value in right))
    if left_magnitude == 0 or right_magnitude == 0:
        raise RuntimeError("Cosine similarity is undefined for a zero vector.")
    return sum(a * b for a, b in zip(left, right, strict=True)) / (
        left_magnitude * right_magnitude
    )
