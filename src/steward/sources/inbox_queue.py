"""INBOX.md: the list of Inbox files waiting to be filed on the Steward computer."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from steward.sources.inbox_context import SourceInboxContextRepository
from steward.sources.models import Source
from steward.sources.repository import SourceRepository

if TYPE_CHECKING:
    from steward.roots import SourceRoot, SourceRootProfileRepository, SourceRootRepository

QUEUE_FILENAME = "INBOX.md"
# Guidance files Codex should read before filing into a root, when present.
ROOT_GUIDANCE_FILENAMES = ("AGENTS.md", "COURSE_WORKFLOWS.md")
_ORIGINS = {"telegram": "Telegram", "gmail": "Gmail", "drive": "Google Drive"}


@dataclass(frozen=True, slots=True)
class InboxQueueEntry:
    source: Source
    received_via: str
    intended_root: "SourceRoot | None"
    note: str | None
    guidance: tuple[Path, ...]


class InboxQueue:
    """Maintain one human- and Codex-readable list of unfiled Inbox files.

    Steward writes only this file inside its own Inbox. It never moves the
    listed originals; whoever files them (usually Codex) does, and a filed file
    drops off the list at the next refresh because it is no longer in the Inbox.
    """

    def __init__(
        self,
        sources: SourceRepository,
        inbox_dir: Path,
        *,
        roots: "SourceRootRepository | None" = None,
        inbox_contexts: SourceInboxContextRepository | None = None,
        profiles: "SourceRootProfileRepository | None" = None,
    ) -> None:
        self._sources = sources
        self._inbox = inbox_dir.resolve()
        self._roots = roots
        self._inbox_contexts = inbox_contexts
        self._profiles = profiles

    @property
    def path(self) -> Path:
        return self._inbox / QUEUE_FILENAME

    def pending(self) -> tuple[InboxQueueEntry, ...]:
        roots = self._roots.list_all() if self._roots is not None else []
        waiting = sorted(
            (
                source for source in self._sources.list_active()
                if source.path.resolve().parent == self._inbox and source.path.is_file()
            ),
            key=lambda source: (source.first_seen_at, source.id or 0),
        )
        entries = []
        for source in waiting:
            context = (
                self._inbox_contexts.get(source.id)
                if self._inbox_contexts is not None and source.id is not None else None
            )
            root = next(
                (item for item in roots if context is not None and item.id == context.intended_root_id), None
            )
            origin = context.capture_origin if context is not None else source.path.name.split("-", 1)[0]
            entries.append(InboxQueueEntry(
                source,
                _ORIGINS.get(origin, origin.title() or "Unknown"),
                root,
                context.user_context if context is not None else None,
                self._guidance(root) if root is not None else (),
            ))
        return tuple(entries)

    def refresh(self) -> Path:
        """Rewrite INBOX.md only when its content changes, to avoid needless syncs."""

        content = self.render(self.pending())
        self._inbox.mkdir(parents=True, exist_ok=True)
        if self.path.is_file() and self.path.read_text(encoding="utf-8") == content:
            return self.path
        temporary = self.path.with_suffix(".md.tmp")
        temporary.write_text(content, encoding="utf-8")
        temporary.replace(self.path)
        return self.path

    @staticmethod
    def render(entries: tuple[InboxQueueEntry, ...]) -> str:
        lines = [
            "# Steward Inbox",
            "",
            "Files sent to Steward that are waiting to be filed. Steward rewrites this list",
            "whenever the Inbox changes, so edits here are lost.",
            "",
            "**For Codex:** move each file into its intended root, following that root's",
            "guidance files. If no root is given, ask the owner. Do not change file contents.",
            "Steward notices the moves on its next scan.",
            "",
        ]
        if not entries:
            lines.append("Nothing is waiting.")
            return "\n".join(lines) + "\n"
        lines.append(f"{len(entries)} file{'s' if len(entries) != 1 else ''} waiting.")
        for index, entry in enumerate(entries, start=1):
            source = entry.source
            lines.extend(["", f"## {index}. {source.path.name}", ""])
            lines.append(f"- File: `{source.path}`")
            lines.append(f"- Received: {_timestamp(source.first_seen_at)} via {entry.received_via}")
            lines.append(f"- Type: {source.source_type.value}")
            if entry.intended_root is not None:
                lines.append(
                    f"- Intended root: {entry.intended_root.name} (`{entry.intended_root.path}`)"
                )
            else:
                lines.append("- Intended root: not given")
            if entry.note:
                lines.append(f"- Note: {' '.join(entry.note.split())}")
            for guidance in entry.guidance:
                lines.append(f"- Guidance: `{guidance}`")
        return "\n".join(lines) + "\n"

    def _guidance(self, root: "SourceRoot") -> tuple[Path, ...]:
        found: list[Path] = []
        profile = (
            self._profiles.get(root.id) if self._profiles is not None and root.id is not None else None
        )
        relative_paths = (*(profile.guidance_paths if profile is not None else ()), *ROOT_GUIDANCE_FILENAMES)
        for relative in relative_paths:
            candidate = root.path / relative
            if candidate.is_file() and candidate not in found:
                found.append(candidate)
        return tuple(found)


def _timestamp(value: datetime) -> str:
    return value.astimezone().strftime("%Y-%m-%d %H:%M")
