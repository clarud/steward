"""Application-level use cases composed from Steward domain services."""

from __future__ import annotations

from typing import NotRequired, Protocol, TypedDict

from steward.answer import AnswerCitation
from steward.capture import InboxCaptureService
from pathlib import Path
from steward.events import IncomingEvent
from steward.intent import Intent, IntentResolver


TEXT_QUESTION_REQUIRED = "Send a text question and I will search your local knowledge."


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

    def handle(self, event: IncomingEvent) -> str:
        """Return a response without knowing which external platform sent it."""

        if not event.text or not event.text.strip():
            return TEXT_QUESTION_REQUIRED

        result = self._graph.invoke(
            {"question": event.text},
            {"configurable": {"thread_id": f"{event.platform}:{event.chat_id}"}},
        )
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


class StewardCaptureApplication:
    """Explicitly preserve text supplied with Telegram's /save command."""

    def __init__(self, capture_service: InboxCaptureService) -> None:
        self._capture_service = capture_service

    def handle(self, event: IncomingEvent) -> str:
        text = (event.text or "").partition(" ")[2].strip()
        if not text:
            return "Use /save followed by the text you want Steward to keep."
        result = self._capture_service.capture_text(
            IncomingEvent(
                id=event.id, platform=event.platform, chat_id=event.chat_id,
                message_id=event.message_id, reply_to_id=event.reply_to_id,
                timestamp=event.timestamp, text=text, attachments=event.attachments,
            )
        )
        if result.duplicate:
            return f"Already saved: {result.source.path}"
        return f"Saved to Inbox: {result.source.path}"

    def handle_file(self, event: IncomingEvent, original_path: Path) -> str:
        """Preserve a document already downloaded by a transport adapter."""

        result = self._capture_service.capture_file(event, original_path)
        if result.duplicate:
            return f"Already saved: {result.source.path}"
        return f"Saved to Inbox: {result.source.path}"


class StewardEventApplication:
    """Route normalized events through one explicit intent decision."""

    def __init__(
        self,
        question_application: StewardQuestionApplication,
        capture_application: StewardCaptureApplication,
        intent_resolver: IntentResolver | None = None,
    ) -> None:
        self._question_application = question_application
        self._capture_application = capture_application
        self._intent_resolver = intent_resolver or IntentResolver()

    def handle(self, event: IncomingEvent) -> str:
        decision = self._intent_resolver.resolve(event)
        if decision.primary_intent is Intent.ASK:
            return self._question_application.handle(event)
        if decision.primary_intent is Intent.CAPTURE:
            return self._capture_application.handle(event)
        return "I do not yet know how to safely handle that request."

    def handle_file(self, event: IncomingEvent, original_path: Path) -> str:
        """Documents are deterministic capture signals after adapter validation."""

        return self._capture_application.handle_file(event, original_path)
