"""Route each Telegram message by its command or button; plain text gets a choice."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from steward.app.files import HELP_TEXT, StewardFilesApplication
from steward.app.intake import StewardIntakeApplication
from steward.app.question import StewardQuestionApplication
from steward.app.search import StewardSearchApplication
from steward.events import IncomingEvent
from steward.presentation import PresentedReply, ReplyAction


class StewardEventApplication:
    """No guessing: commands and buttons decide which flow runs."""

    def __init__(
        self,
        files: StewardFilesApplication,
        search: StewardSearchApplication,
        intake: StewardIntakeApplication,
        question: StewardQuestionApplication,
    ) -> None:
        self._files = files
        self._search = search
        self._intake = intake
        self._question = question

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
        if command == "/find":
            return self._search.find(argument) if argument else "Use /find followed by a few words."
        if command == "/ask":
            if not argument:
                return "Use /ask followed by your question."
            return self._question.handle(replace(event, text=argument))
        if command == "/note":
            return self._intake.begin_note(event, argument) if argument else "Use /note followed by the note."
        return "I don't know that command. Try /help."
