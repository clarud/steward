"""Route each Telegram message by its command or button; plain text gets a choice."""

from __future__ import annotations

from pathlib import Path

from steward.app.answers import StewardAnswersApplication
from steward.app.files import HELP_TEXT, StewardFilesApplication
from steward.app.intake import StewardIntakeApplication
from steward.app.search import parse_find
from steward.events import IncomingEvent
from steward.presentation import PresentedReply, ReplyAction
from steward.roots import SourceRootRepository


class StewardEventApplication:
    """No guessing: commands and buttons decide which flow runs."""

    def __init__(
        self,
        files: StewardFilesApplication,
        intake: StewardIntakeApplication,
        answers: StewardAnswersApplication | None,
        roots: SourceRootRepository,
    ) -> None:
        self._files = files
        self._intake = intake
        self._answers = answers
        self._roots = roots

    def handle(self, event: IncomingEvent) -> str | PresentedReply:
        text = (event.text or "").strip()
        if text.startswith("/"):
            return self._command(event)
        for followup in (self._intake.note_followup, self._files.file_question):
            response = followup(event)
            if response is not None:
                return response
        if not text:
            return HELP_TEXT
        return PresentedReply(
            "What should I do with this?",
            (
                ReplyAction("🔎 Find", f"/find {text}"),
                ReplyAction("💬 Ask", f"/ask {text}"),
                ReplyAction("📝 Save as note", f"/note {text}"),
            ),
        )

    def handle_file(self, event: IncomingEvent, original_path: Path) -> PresentedReply:
        return self._intake.begin_file(event, original_path)

    def _command(self, event: IncomingEvent) -> str | PresentedReply:
        for application in (self._intake, self._files):
            response = application.handle_command(event)
            if response is not None:
                return response
        parts = (event.text or "").strip().split(maxsplit=1)
        command = parts[0].partition("@")[0].casefold()
        argument = parts[1].strip() if len(parts) > 1 else ""
        if command in {"/find", "/ask", "/ask_removed"} and self._answers is None:
            return "No model is configured on this computer; set one in .env and restart the bot."
        if command == "/find":
            if not argument:
                return "Use /find followed by a few words."
            parsed = parse_find(argument, self._roots)
            if isinstance(parsed, str):
                return parsed
            return self._answers.find(event.chat_id, *parsed)  # type: ignore[union-attr]
        if command == "/ask":
            if not argument:
                return "Use /ask followed by your question."
            return self._answers.ask(event.chat_id, argument)  # type: ignore[union-attr]
        if command == "/ask_removed":
            return self._answers.removed(event.chat_id)  # type: ignore[union-attr]
        if command == "/note":
            return self._intake.begin_note(event, argument) if argument else "Use /note followed by the note."
        return "I don't know that command. Try /help."
