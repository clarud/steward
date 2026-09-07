"""The first LangGraph workflow: retrieve local evidence, then answer."""

from __future__ import annotations

import re
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
    messages: NotRequired[list[dict[str, str]]]
    resolved_question: NotRequired[str]
    referents: NotRequired[dict[str, str]]
    recent_source_ids: NotRequired[list[int]]
    current_workspace_id: NotRequired[str | None]
    recent_concepts: NotRequired[list[str]]
    recent_records: NotRequired[list[str]]


def build_retrieval_answer_graph(
    retriever: Retriever, answer_service: AnswerService, *, retrieval_limit: int = 5,
    checkpointer: object | None = None,
):
    """Compile the minimal retrieve → answer workflow.

    ``retrieved_hits`` is transient working state for this in-memory graph.
    ``retrieved_fragment_ids`` is the compact, inspectable representation that
    later persistence-oriented graph state will retain.
    """

    if retrieval_limit <= 0:
        raise ValueError("retrieval_limit must be positive.")

    def prepare(state: RetrievalAnswerState) -> dict[str, object]:
        previous = next(
            (m["text"] for m in reversed(state.get("messages", [])) if m["role"] == "user"),
            None,
        )
        question = state["question"]
        referential = bool(re.search(r"\b(that|this|it|they|them|those)\b", question, re.I))
        resolved = (
            f"Previous question: {previous}\nCurrent question: {question}"
            if previous and referential
            else question
        )
        return {
            "resolved_question": resolved,
            "referents": {"that": previous} if previous and referential else {},
            "messages": (state.get("messages", []) + [{"role": "user", "text": question}])[-10:],
        }

    def retrieve(state: RetrievalAnswerState) -> dict[str, object]:
        hits = retriever.search(
            state.get("resolved_question", state["question"]), limit=retrieval_limit
        )
        fragment_ids = [
            hit.fragment.id
            for hit in hits
            if hit.fragment.id is not None
        ]
        return {"retrieved_fragment_ids": fragment_ids}

    def has_evidence(state: RetrievalAnswerState) -> Literal["answer", "no_evidence"]:
        return "answer" if state.get("retrieved_fragment_ids") else "no_evidence"

    def answer(state: RetrievalAnswerState) -> dict[str, object]:
        question = state.get("resolved_question", state["question"])
        # Retrieval results include source text, so keep them execution-local rather
        # than placing them in checkpointed graph state. Re-running this read-only
        # query lets the answer node rebuild context while persisted state remains compact.
        result = answer_service.answer_from_hits(
            question, retriever.search(question, limit=retrieval_limit)
        )
        return {
            "answer": result.text,
            "citations": result.citations,
            "messages": (state.get("messages", []) + [{"role": "assistant", "text": result.text}])[-10:],
            "recent_source_ids": [citation.fragment_id for citation in result.citations],
        }

    def no_evidence(state: RetrievalAnswerState) -> dict[str, object]:
        result = answer_service.answer_from_hits(state["question"], ())
        return {
            "answer": result.text,
            "citations": result.citations,
            "messages": (state.get("messages", []) + [{"role": "assistant", "text": result.text}])[-10:],
            "recent_source_ids": [],
        }

    builder = StateGraph(RetrievalAnswerState)
    builder.add_node("prepare", prepare)
    builder.add_node("retrieve", retrieve)
    builder.add_node("answer", answer)
    builder.add_node("no_evidence", no_evidence)
    builder.add_edge(START, "prepare")
    builder.add_edge("prepare", "retrieve")
    builder.add_conditional_edges(
        "retrieve",
        has_evidence,
        {"answer": "answer", "no_evidence": "no_evidence"},
    )
    builder.add_edge("answer", END)
    builder.add_edge("no_evidence", END)
    return builder.compile(checkpointer=checkpointer)
