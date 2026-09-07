"""The first LangGraph workflow: retrieve local evidence, then answer."""

from __future__ import annotations

from typing import Literal, NotRequired, TypedDict

from langgraph.graph import END, START, StateGraph

from steward.answer import AnswerCitation, AnswerService
from steward.answer.service import Retriever
from steward.retrieval import HybridSearchHit


class RetrievalAnswerState(TypedDict):
    """Shared state for the first deterministic Steward graph."""

    question: str
    retrieved_fragment_ids: NotRequired[list[int]]
    answer: NotRequired[str]
    citations: NotRequired[tuple[AnswerCitation, ...]]
    retrieved_hits: NotRequired[tuple[HybridSearchHit, ...]]


def build_retrieval_answer_graph(
    retriever: Retriever, answer_service: AnswerService, *, retrieval_limit: int = 5
):
    """Compile the minimal retrieve → answer workflow.

    ``retrieved_hits`` is transient working state for this in-memory graph.
    ``retrieved_fragment_ids`` is the compact, inspectable representation that
    later persistence-oriented graph state will retain.
    """

    if retrieval_limit <= 0:
        raise ValueError("retrieval_limit must be positive.")

    def retrieve(state: RetrievalAnswerState) -> dict[str, object]:
        hits = retriever.search(state["question"], limit=retrieval_limit)
        fragment_ids = [
            hit.fragment.id
            for hit in hits
            if hit.fragment.id is not None
        ]
        return {
            "retrieved_hits": hits,
            "retrieved_fragment_ids": fragment_ids,
        }

    def has_evidence(state: RetrievalAnswerState) -> Literal["answer", "no_evidence"]:
        return "answer" if state.get("retrieved_fragment_ids") else "no_evidence"

    def answer(state: RetrievalAnswerState) -> dict[str, object]:
        result = answer_service.answer_from_hits(
            state["question"], state.get("retrieved_hits", ())
        )
        return {"answer": result.text, "citations": result.citations}

    def no_evidence(state: RetrievalAnswerState) -> dict[str, object]:
        result = answer_service.answer_from_hits(state["question"], ())
        return {"answer": result.text, "citations": result.citations}

    builder = StateGraph(RetrievalAnswerState)
    builder.add_node("retrieve", retrieve)
    builder.add_node("answer", answer)
    builder.add_node("no_evidence", no_evidence)
    builder.add_edge(START, "retrieve")
    builder.add_conditional_edges(
        "retrieve",
        has_evidence,
        {"answer": "answer", "no_evidence": "no_evidence"},
    )
    builder.add_edge("answer", END)
    builder.add_edge("no_evidence", END)
    return builder.compile()
