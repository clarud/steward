"""Application-level use cases composed from Steward domain services."""

from __future__ import annotations

from typing import NotRequired, Protocol, TypedDict

from steward.answer import AnswerCitation
from steward.events import IncomingEvent


TEXT_QUESTION_REQUIRED = "Send a text question and I will search your local knowledge."


class QuestionGraph(Protocol):
    """The small graph interface needed by the text-question use case."""

    def invoke(self, input: dict[str, str]) -> "QuestionResult": ...


class QuestionResult(TypedDict):
    """The part of graph output that the transport needs to return."""

    answer: str
    citations: NotRequired[tuple[AnswerCitation, ...]]


class StewardQuestionApplication:
    """Answer one normalized text event through Steward's retrieval graph."""

    def __init__(self, graph: QuestionGraph) -> None:
        self._graph = graph

    def handle(self, event: IncomingEvent) -> str:
        """Return a response without knowing which external platform sent it."""

        if not event.text or not event.text.strip():
            return TEXT_QUESTION_REQUIRED

        result = self._graph.invoke({"question": event.text})
        return self._format_response(result)

    @staticmethod
    def _format_response(result: QuestionResult) -> str:
        """Keep generated citation keys useful on a text-only transport."""

        citations = result.get("citations", ())
        if not citations:
            return result["answer"]

        source_lines = []
        for citation in citations:
            heading = citation.heading or "Preamble"
            source_lines.append(
                f"[{citation.key}] {citation.source_path}:"
                f"{citation.location} [{heading}]"
            )
        return f"{result['answer']}\n\nSources:\n" + "\n".join(source_lines)
