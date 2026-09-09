"""Telegram-safe rendering for Steward's transport-neutral replies."""

from __future__ import annotations

from dataclasses import dataclass
from html import escape

from steward.presentation import PresentedReply, ReplyAction


@dataclass(frozen=True, slots=True)
class RenderedTelegramReply:
    """Escaped HTML text and compact action rows ready for Telegram."""

    text: str
    rows: tuple[tuple[ReplyAction, ...], ...]


class TelegramPresenter:
    """Keep Telegram formatting and button constraints out of application services."""

    # Stay below Telegram's 4096-character limit, leaving room for transport
    # additions and avoiding splits through most escaped entities.
    _MAX_MESSAGE_CHARACTERS = 3_800

    _LABELS = {
        "Allow external model": "Use external",
        "Use local model": "Use local",
        "Save (external allowed)": "Save",
        "Save (local only)": "Save",
        "Save (no model)": "Save",
        "Create deadline event": "Create",
        "Create event": "Create",
        "Keep this reviewed note": "Keep",
        "Refresh derived text": "Refresh",
        "Rebuild local index": "Rebuild",
        "Unregister metadata": "Unregister",
        "Keep registered": "Cancel",
        "Reopen retry budget": "Retry",
        "Keep terminal": "Keep closed",
        "Apply correction": "Apply",
        "Accept record": "Accept",
        "Accept task": "Accept",
        "Add reference": "Add",
        "Do not keep": "Discard",
        "Keep in Inbox": "Inbox",
    }
    _MAX_LABEL_LENGTH = 18

    def render(self, response: str | PresentedReply) -> RenderedTelegramReply:
        """Escape dynamic text before Telegram interprets it as HTML."""

        if not isinstance(response, PresentedReply):
            return RenderedTelegramReply(escape(str(response)), ())
        heading = response.title
        body = response.text
        if heading is None:
            heading, body = self._split_heading(body)
        prefix = f"{escape(response.icon)} " if response.icon else ""
        rendered = f"{prefix}<b>{escape(heading)}</b>"
        if body:
            rendered += f"\n\n{escape(body)}"
        return RenderedTelegramReply(rendered, self.action_rows(response.actions))

    def render_many(self, response: str | PresentedReply) -> tuple[RenderedTelegramReply, ...]:
        """Render one response into Telegram-sized messages.

        Content is escaped before splitting. The splitter preserves the exact
        escaped text and avoids ending a chunk inside an HTML entity. Buttons
        belong only to the final message so a user never sees duplicate
        approval controls after a long answer.
        """

        rendered = self.render(response)
        chunks = self._split_html(rendered.text)
        if len(chunks) == 1:
            return (rendered,)
        return tuple(
            RenderedTelegramReply(
                text,
                rendered.rows if index == len(chunks) - 1 else (),
            )
            for index, text in enumerate(chunks)
        )

    def action_rows(self, actions: tuple[ReplyAction, ...]) -> tuple[tuple[ReplyAction, ...], ...]:
        """Make visually short buttons and avoid one unusably wide button row."""

        compact = tuple(
            ReplyAction(self.compact_label(action.label), action.command)
            for action in actions
        )
        return tuple(compact[index:index + 2] for index in range(0, len(compact), 2))

    def compact_label(self, label: str) -> str:
        """Retain the action, not its explanatory text, in Telegram's small buttons."""

        normalized = " ".join(label.split())
        if normalized.casefold().startswith("import "):
            return "Import"
        compact = self._LABELS.get(normalized, normalized)
        if len(compact) <= self._MAX_LABEL_LENGTH:
            return compact
        return compact[: self._MAX_LABEL_LENGTH - 1].rstrip() + "…"

    @staticmethod
    def _split_heading(text: str) -> tuple[str, str]:
        """Give existing reply cards a useful heading without changing their services."""

        first, separator, remainder = text.strip().partition("\n")
        if not separator or len(first) > 88:
            return "Steward", text.strip()
        return first.rstrip(":"), remainder.lstrip()

    @classmethod
    def _split_html(cls, text: str) -> tuple[str, ...]:
        if len(text) <= cls._MAX_MESSAGE_CHARACTERS:
            return (text,)
        chunks: list[str] = []
        remaining = text
        while len(remaining) > cls._MAX_MESSAGE_CHARACTERS:
            limit = cls._MAX_MESSAGE_CHARACTERS
            boundary = max(remaining.rfind("\n", 0, limit), remaining.rfind(" ", 0, limit))
            cut = boundary if boundary > limit // 2 else limit
            ampersand = remaining.rfind("&", 0, cut)
            if ampersand != -1 and remaining.rfind(";", ampersand, cut) == -1:
                cut = ampersand
            if cut == 0:
                cut = limit
            chunks.append(remaining[:cut])
            remaining = remaining[cut:]
        chunks.append(remaining)
        return tuple(chunks)
