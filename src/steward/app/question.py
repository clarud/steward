"""Grounded questions answered from retrieved local fragments."""

from __future__ import annotations

from typing import NotRequired, Protocol, TypedDict

from steward.answer import AnswerCitation
from steward.events import IncomingEvent
from steward.presentation import PresentedReply


TEXT_QUESTION_REQUIRED = "Send a text question and I will search your saved files."


class QuestionGraph(Protocol):
    """The small graph interface needed by the text-question use case."""

    def invoke(self, input: dict[str, str], config: dict[str, object]) -> "QuestionResult": ...


class QuestionResult(TypedDict):
    """The part of graph output that the transport needs to return."""

    answer: str
    citations: NotRequired[tuple[AnswerCitation, ...]]


class StewardQuestionApplication:
    """Answer one normalized text event through Steward's retrieval graph."""

    def __init__(self, graph: QuestionGraph) -> None:
        self._graph = graph

    def handle(self, event: IncomingEvent) -> str | PresentedReply:
        """Return a response without knowing which external platform sent it."""

        if not event.text or not event.text.strip():
            return TEXT_QUESTION_REQUIRED
        question = event.text.strip()
        reply_text = (event.reply_text or "").strip()
        if reply_text:
            # A reply is stronger evidence of the intended referent than the
            # latest graph turn. Keep it bounded and visibly user-supplied so
            # it cannot masquerade as a system instruction or a source.
            context = reply_text[:2_000]
            suffix = "…" if len(reply_text) > len(context) else ""
            question = f"Reply context (user-supplied): {context}{suffix}\n\nCurrent question: {question}"

        result = self._graph.invoke(
            {"question": question},
            {"configurable": {"thread_id": f"{event.platform}:{event.chat_id}"}},
        )
        return self._format_response(result)

    @staticmethod
    def _format_response(result: QuestionResult) -> str | PresentedReply:
        """Render grounded evidence as a concise transport-neutral answer card."""

        citations = result.get("citations", ())
        if not citations:
            return result["answer"]

        source_lines = []
        for citation in citations:
            heading = citation.heading or "Preamble"
            source_lines.append(
                f"[{citation.key}] {citation.source_path.name}:"
                f"{citation.location} [{heading}]"
            )
        return PresentedReply(
            f"{result['answer']}\n\nSources:\n" + "\n".join(source_lines),
            title="Answer from your saved material",
            icon="🧠",
        )
