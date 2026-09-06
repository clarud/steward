from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from pathlib import Path

from steward.extraction import MarkdownExtractor, SourceFragmentRepository
from steward.retrieval import (
    HybridRetriever,
    LexicalSearchService,
    SemanticSearchService,
    SQLiteSemanticIndex,
)
from steward.sources import SourceRepository
from steward.sources.service import SourceService
from steward.storage import initialize_database


class FakeEmbeddingProvider:
    """A small deterministic vector space for retrieval tests."""

    model_name = "test-meaning-v1"

    def embed_documents(self, texts: Sequence[str]) -> tuple[tuple[float, ...], ...]:
        return tuple(self._embed(text) for text in texts)

    def embed_query(self, query: str) -> tuple[float, ...]:
        return self._embed(query)

    @staticmethod
    def _embed(text: str) -> tuple[float, ...]:
        normalized = text.casefold()
        if any(word in normalized for word in ("tlb", "translation", "cache")):
            return (1.0, 0.0)
        return (0.0, 1.0)


def _build_indexed_vault(tmp_path: Path) -> tuple[
    Path, SourceRepository, SourceFragmentRepository, SQLiteSemanticIndex
]:
    database_path = tmp_path / "steward.db"
    initialize_database(database_path)
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "virtual-memory.md").write_text(
        "# TLB\nA TLB caches recently used address translations.", encoding="utf-8"
    )
    (vault / "scheduler.md").write_text(
        "# Scheduling\nA scheduler runs queued jobs.", encoding="utf-8"
    )
    source_repository = SourceRepository(database_path)
    fragment_repository = SourceFragmentRepository(database_path)
    semantic_index = SQLiteSemanticIndex(database_path, FakeEmbeddingProvider())
    SourceService(
        source_repository=source_repository,
        fragment_repository=fragment_repository,
        markdown_extractor=MarkdownExtractor(),
        semantic_index=semantic_index,
    ).scan_markdown_root(vault)
    return database_path, source_repository, fragment_repository, semantic_index


def test_semantic_index_persists_and_finds_a_meaningful_match(tmp_path: Path) -> None:
    database_path, source_repository, _, semantic_index = _build_indexed_vault(tmp_path)

    hits = SemanticSearchService(source_repository, semantic_index).search(
        "the little cache for address translation"
    )

    assert hits[0].source.path.name == "virtual-memory.md"
    assert hits[0].fragment.heading == "TLB"
    assert hits[0].score == 1.0
    with sqlite3.connect(database_path) as connection:
        rows = connection.execute(
            "SELECT model_name, dimension FROM source_fragment_embeddings"
        ).fetchall()
    assert rows == [("test-meaning-v1", 2), ("test-meaning-v1", 2)]


def test_hybrid_retriever_rewards_a_fragment_found_by_both_methods(tmp_path: Path) -> None:
    _, source_repository, fragment_repository, semantic_index = _build_indexed_vault(tmp_path)
    hybrid = HybridRetriever(
        LexicalSearchService(source_repository, fragment_repository),
        SemanticSearchService(source_repository, semantic_index),
    )

    hits = hybrid.search("TLB")

    assert hits[0].source.path.name == "virtual-memory.md"
    assert hits[0].lexical_score is not None
    assert hits[0].semantic_score == 1.0


def test_semantic_index_replaces_vectors_when_source_fragments_are_replaced(
    tmp_path: Path,
) -> None:
    database_path, source_repository, fragment_repository, semantic_index = _build_indexed_vault(
        tmp_path
    )
    source = source_repository.list_active()[0]
    source.path.write_text("# Paging\nPage tables map addresses.", encoding="utf-8")
    stored_fragments = fragment_repository.replace_for_source(
        MarkdownExtractor().extract(source)
    )
    semantic_index.replace_for_source(stored_fragments)

    with sqlite3.connect(database_path) as connection:
        source_embedding_count = connection.execute(
            """
            SELECT COUNT(*)
            FROM source_fragment_embeddings AS sfe
            JOIN source_fragments AS sf ON sf.id = sfe.fragment_id
            WHERE sf.source_id = ?
            """,
            (source.id,),
        ).fetchone()[0]
    assert source_embedding_count == 1
