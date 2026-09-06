"""Services for retrieving evidence from Steward's local sources."""

from steward.retrieval.lexical import LexicalSearchHit, LexicalSearchService
from steward.retrieval.hybrid import HybridRetriever, HybridSearchHit
from steward.retrieval.semantic import (
    EmbeddingProvider,
    SemanticFragmentHit,
    SemanticIndex,
    SemanticSearchHit,
    SemanticSearchService,
    SentenceTransformerEmbeddingProvider,
    SQLiteSemanticIndex,
)

__all__ = [
    "EmbeddingProvider",
    "HybridRetriever",
    "HybridSearchHit",
    "LexicalSearchHit",
    "LexicalSearchService",
    "SemanticFragmentHit",
    "SemanticIndex",
    "SemanticSearchHit",
    "SemanticSearchService",
    "SentenceTransformerEmbeddingProvider",
    "SQLiteSemanticIndex",
]
