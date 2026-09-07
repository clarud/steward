from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from steward.answer import AnswerService, ContextBuilder
from steward.graphs import build_retrieval_answer_graph
from steward.retrieval import HybridSearchHit
from steward.sources import Source, SourceType
from steward.extraction import SourceFragment


@dataclass
class FakeRetriever:
    hits: tuple[HybridSearchHit, ...]
    received_limit: int | None = None

    def search(self, query: str, *, limit: int = 5) -> tuple[HybridSearchHit, ...]:
        self.received_limit = limit
        return self.hits


@dataclass
class FakeGateway:
    calls: int = 0

    def generate(self, *, instructions: str, input_text: str) -> str:
        self.calls += 1
        return "A TLB caches translations. [F1]"


def _hit() -> HybridSearchHit:
    timestamp = datetime(2026, 9, 7, tzinfo=UTC)
    source = Source(
        id=1,
        path=Path("C:/vault/virtual-memory.md"),
        content_hash="a" * 64,
        source_type=SourceType.MARKDOWN,
        size_bytes=100,
        modified_at=timestamp,
        first_seen_at=timestamp,
        last_seen_at=timestamp,
    )
    fragment = SourceFragment(
        id=4,
        source_id=1,
        heading="TLB",
        ordinal=0,
        text="A TLB caches recently used address translations.",
        location="lines 9-12",
    )
    return HybridSearchHit(source, fragment, 0.03, -1.2, 0.8)


def test_graph_retrieves_then_answers_and_records_fragment_ids() -> None:
    retriever = FakeRetriever((_hit(),))
    gateway = FakeGateway()
    answer_service = AnswerService(retriever, ContextBuilder(), gateway)
    graph = build_retrieval_answer_graph(
        retriever, answer_service, retrieval_limit=3
    )

    result = graph.invoke({"question": "What does a TLB do?"})

    assert result["retrieved_fragment_ids"] == [4]
    assert result["answer"] == "A TLB caches translations. [F1]"
    assert result["citations"][0].fragment_id == 4
    assert retriever.received_limit == 3
    assert gateway.calls == 1


def test_graph_routes_to_no_evidence_without_calling_the_model() -> None:
    retriever = FakeRetriever(())
    gateway = FakeGateway()
    answer_service = AnswerService(retriever, ContextBuilder(), gateway)
    graph = build_retrieval_answer_graph(retriever, answer_service)

    result = graph.invoke({"question": "What did I decide about Redis?"})

    assert result["retrieved_fragment_ids"] == []
    assert result["answer"] == "I don't have enough local information to answer that."
    assert result["citations"] == ()
    assert gateway.calls == 0
