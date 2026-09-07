from datetime import UTC, datetime
from pathlib import Path

from steward.answer import AnswerCitation
from steward.application import StewardQuestionApplication, TEXT_QUESTION_REQUIRED
from steward.events import IncomingEvent


class FakeGraph:
    def __init__(self) -> None:
        self.inputs: list[dict[str, str]] = []

    def invoke(self, input: dict[str, str], config=None) -> dict[str, str]:
        self.inputs.append(input)
        return {"answer": "A TLB caches address translations. [F1]"}


def make_event(*, text: str | None) -> IncomingEvent:
    return IncomingEvent(
        id="telegram:42",
        platform="telegram",
        chat_id="100",
        message_id="7",
        reply_to_id=None,
        timestamp=datetime(2026, 9, 7, tzinfo=UTC),
        text=text,
    )


def test_question_application_passes_normalized_text_to_graph() -> None:
    graph = FakeGraph()

    response = StewardQuestionApplication(graph).handle(make_event(text="What is a TLB?"))

    assert graph.inputs == [{"question": "What is a TLB?"}]
    assert response == "A TLB caches address translations. [F1]"


def test_question_application_does_not_invoke_graph_for_empty_text() -> None:
    graph = FakeGraph()

    response = StewardQuestionApplication(graph).handle(make_event(text="   "))

    assert response == TEXT_QUESTION_REQUIRED
    assert graph.inputs == []


def test_question_application_includes_source_details_for_citations() -> None:
    class CitedGraph:
        def invoke(self, input: dict[str, str], config=None):
            return {
                "answer": "A TLB caches translations. [F1]",
                "citations": (
                    AnswerCitation(
                        key="F1",
                        fragment_id=3,
                        source_path=Path("vault/virtual-memory.md"),
                        heading="TLB",
                        location="lines 4-6",
                    ),
                ),
            }

    response = StewardQuestionApplication(CitedGraph()).handle(
        make_event(text="What is a TLB?")
    )

    assert response == (
        "A TLB caches translations. [F1]\n\n"
        f"Sources:\n[F1] {Path('vault/virtual-memory.md')}:lines 4-6 [TLB]"
    )
