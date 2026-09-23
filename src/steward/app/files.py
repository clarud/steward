"""Browsing and reading files from Telegram: roots, folders, file cards, and content."""

from __future__ import annotations

from collections import Counter
from typing import TYPE_CHECKING
from pathlib import Path, PurePosixPath

from steward.events import IncomingEvent
from steward.extraction import SourceFragmentRepository
from steward.presentation import PresentedReply, ReplyAction
from steward.reviews import ReviewContextRepository
from steward.roots import SourceRoot, SourceRootRepository
from steward.sources import Source, SourceRepository
from steward.sources.export import SourceExportService
from steward.sources.inbox_context import SourceInboxContextRepository

if TYPE_CHECKING:
    from steward.app.answers import StewardAnswersApplication

PAGE_SIZE = 10
HELP_TEXT = (
    "Steward finds your files and brings them to you.\n\n"
    "/find WORDS — find a file (add --type pdf or --root \"Y4S1\" to narrow)\n"
    "/ask QUESTION — answer from your files, with sources\n"
    "/sources — browse your folders\n"
    "/inbox — uploads waiting to be filed\n\n"
    "Send a file or note to save it to the Inbox. On a file card, reply with a "
    "question to ask about that file. Saved uploads are listed in INBOX.md on your "
    "computer for Codex to file; Steward picks up the moves on its next scan."
)


def _recovery_guidance(source: Source) -> str:
    """Explain likely local fixes for a file with no extracted text."""
    suffix = source.path.suffix.casefold()
    kind = source.source_type.value
    if kind == "pdf":
        return (
            "PDFs: native text is tried first. A scanned PDF also needs Poppler (`pdftoppm`) and "
            "Tesseract installed. Password-protected or damaged PDFs must be fixed first."
        )
    if kind == "image":
        return "Images need Tesseract installed. Small, blurred, rotated, or handwritten text may not be read."
    if kind == "docx":
        return (
            "DOCX: the file must be a real `.docx` (not a renamed `.doc`), not password-protected or "
            "damaged, and not an empty OneDrive placeholder."
        )
    if kind == "html":
        return "HTML: Steward reads visible UTF-8 text, not content loaded later by JavaScript."
    if suffix == ".eml":
        return "Email: Steward reads the message body; attachments need to be saved as their own files."
    if kind in {"markdown", "plain_text"}:
        return "Text files must be UTF-8. An empty file correctly has no text."
    return "This file type has no text extractor. Export a copy to PDF, DOCX, or Markdown to make it searchable."


class StewardFilesApplication:
    """Deterministic views over registered files; Summarize and Ask hand off to the model flows."""

    def __init__(
        self,
        sources: SourceRepository,
        fragments: SourceFragmentRepository,
        roots: SourceRootRepository,
        inbox_dir: Path,
        *,
        contexts: ReviewContextRepository | None = None,
        inbox_contexts: SourceInboxContextRepository | None = None,
        source_export: SourceExportService | None = None,
        answers: "StewardAnswersApplication | None" = None,
    ) -> None:
        self._sources = sources
        self._fragments = fragments
        self._roots = roots
        self._inbox = inbox_dir.resolve()
        self._contexts = contexts
        self._inbox_contexts = inbox_contexts
        self._export = source_export
        self._answers = answers

    # -- commands ---------------------------------------------------------

    def handle_command(self, event: IncomingEvent) -> str | PresentedReply | None:
        command, argument = _split_command(event.text)
        if command in {"/home", "/start"}:
            return self.home()
        if command == "/help":
            return HELP_TEXT
        if command == "/sources":
            return self.roots_overview()
        if command == "/browse":
            return self.browse(argument)
        if command == "/inbox":
            return self.inbox(_page(argument))
        if command == "/source":
            if not argument.isdigit():
                return "Open a file from /sources or a search result."
            return self._remember(event, int(argument), self.file_card(int(argument)))
        if command == "/source_content":
            parts = argument.split()
            if not 1 <= len(parts) <= 2 or not all(part.isdigit() for part in parts):
                return "Open a file card and choose Read."
            source_id = int(parts[0])
            return self._remember(event, source_id, self.read(source_id, int(parts[1]) if len(parts) == 2 else 1))
        if command == "/send_source":
            if not argument.isdigit():
                return "Open a file card and choose Send original."
            return self.send_original(int(argument))
        if command == "/summarize_source":
            if not argument.isdigit():
                return "Open a file card and choose Summarize."
            return self.summarize(int(argument))
        if command == "/ask_source":
            identifier, _, question = argument.partition(" ")
            if not identifier.isdigit() or self._sources.get_by_id(int(identifier)) is None:
                return "Open a file card and choose Ask."
            if question.strip():
                return self.ask_about(event, int(identifier), question.strip())
            if self._contexts is not None:
                self._contexts.set(event.platform, event.chat_id, "source_question", int(identifier))
            return PresentedReply(
                "What would you like to know about this file? Send your question next.",
                (ReplyAction("Cancel", f"/source {identifier}"),), title="Ask about this file", icon="💬",
            )
        return None

    def file_question(self, event: IncomingEvent) -> str | PresentedReply | None:
        """Answer about one file when the message replies to its card or follows Ask."""
        if self._contexts is None:
            return None
        text = (event.text or "").strip()
        if not text or text.startswith("/"):
            return None
        context = self._contexts.get(event.platform, event.chat_id)
        if context is None or not isinstance(context.identifier, int):
            return None
        replying_to_card = context.kind == "source" and event.reply_to_id is not None
        if context.kind != "source_question" and not replying_to_card:
            return None
        self._contexts.set(event.platform, event.chat_id, "source", context.identifier)
        return self.ask_about(event, context.identifier, text)

    # -- views ------------------------------------------------------------

    def home(self) -> PresentedReply:
        return PresentedReply(
            "Send /find with a few words, /ask with a question, or send a file to save it.",
            (ReplyAction("Browse", "/sources"), ReplyAction("Inbox", "/inbox"), ReplyAction("Help", "/help")),
            title="Steward", icon="🏠",
        )

    def roots_overview(self) -> PresentedReply | str:
        roots = self._roots.list_all()
        active = self._sources.list_active()
        lines = []
        actions = []
        for index, root in enumerate(roots, start=1):
            count = sum(source.path.is_relative_to(root.path) for source in active)
            status = "" if root.health == "available" else f" · {root.health}"
            lines.append(f"{index}. 🗂️ {root.name} · {count} files{status}")
            actions.append(ReplyAction(f"Open {index}", f"/browse {root.id} 1"))
        inbox_count = len(self._waiting_in_inbox())
        lines.append(f"{len(roots) + 1}. 📥 Inbox · {inbox_count} waiting")
        actions.append(ReplyAction(f"Open {len(roots) + 1}", "/inbox"))
        if not roots:
            lines.insert(0, 'No folders yet. On your computer run: steward onboard-root "Name" "C:\\path\\to\\folder"\n')
        return PresentedReply("\n".join(lines), tuple(actions), title="Your files", icon="📚")

    def browse(self, argument: str) -> PresentedReply | str:
        """Show one folder of a root: subfolders first, then files, paginated."""
        root_token, _, rest = argument.strip().partition(" ")
        page_token, _, folder = rest.strip().partition(" ")
        if not root_token.isdigit():
            return "Open a folder from /sources."
        root = next((item for item in self._roots.list_all() if item.id == int(root_token)), None)
        if root is None:
            return "That folder is no longer authorized. Open /sources."
        folder_path = PurePosixPath(folder.strip()) if folder.strip() else PurePosixPath()
        if ".." in folder_path.parts or folder_path.is_absolute():
            return "Open a folder from /sources."
        base = root.path.joinpath(*folder_path.parts).resolve()
        if not base.is_relative_to(root.path.resolve()):
            return "Open a folder from /sources."
        subfolders: Counter[str] = Counter()
        files: list[Source] = []
        for source in self._sources.list_active():
            if not source.path.is_relative_to(base):
                continue
            relative = source.path.relative_to(base)
            if len(relative.parts) == 1:
                files.append(source)
            else:
                subfolders[relative.parts[0]] += 1
        entries: list[tuple[str, str, str]] = [
            (f"📁 {name} ({count})", "folder", name) for name, count in sorted(subfolders.items(), key=lambda item: item[0].casefold())
        ] + [
            (f"📄 {source.path.name}{self._no_text_marker(source)}", "file", str(source.id))
            for source in sorted(files, key=lambda item: item.path.name.casefold())
        ]
        page = max(1, int(page_token)) if page_token.isdigit() else 1
        pages = max(1, (len(entries) + PAGE_SIZE - 1) // PAGE_SIZE)
        page = min(page, pages)
        visible = entries[(page - 1) * PAGE_SIZE:page * PAGE_SIZE]
        title = " / ".join((root.name, *folder_path.parts))
        lines = [f"Page {page} of {pages}"] if pages > 1 else []
        lines.extend(f"{index}. {label}" for index, (label, _, _) in enumerate(visible, start=1))
        if not entries:
            lines.append("This folder has no registered files.")
        actions = []
        for index, (_, kind, value) in enumerate(visible, start=1):
            if kind == "folder":
                actions.append(ReplyAction(f"Open {index}", _browse_command(root, folder_path / value)))
            else:
                actions.append(ReplyAction(f"Open {index}", f"/source {value}"))
        if page > 1:
            actions.append(ReplyAction("Back", _browse_command(root, folder_path, page - 1)))
        if page < pages:
            actions.append(ReplyAction("Next", _browse_command(root, folder_path, page + 1)))
        actions.append(
            ReplyAction("Up", _browse_command(root, folder_path.parent))
            if folder_path.parts else ReplyAction("All folders", "/sources")
        )
        return PresentedReply("\n".join(lines), tuple(actions), title=title, icon="🗂️")

    def inbox(self, page: int = 1) -> PresentedReply | str:
        waiting = self._waiting_in_inbox()
        if not waiting:
            return PresentedReply(
                "Nothing is waiting. Send a file or note to save it here.",
                (ReplyAction("Browse", "/sources"),), title="Inbox", icon="📥",
            )
        pages = max(1, (len(waiting) + PAGE_SIZE - 1) // PAGE_SIZE)
        page = min(max(page, 1), pages)
        visible = waiting[(page - 1) * PAGE_SIZE:page * PAGE_SIZE]
        lines = [f"{len(waiting)} waiting to be filed" + (f" · page {page} of {pages}" if pages > 1 else "")]
        for index, source in enumerate(visible, start=1):
            context = self._inbox_contexts.get(source.id) if self._inbox_contexts is not None and source.id else None
            target = f" → {context.intended_root_name}" if context is not None and context.intended_root_name else ""
            lines.append(f"{index}. {source.path.name}{target}")
        lines.append("\nOn your computer these are listed in Inbox/INBOX.md.")
        actions = [ReplyAction(f"Open {index}", f"/source {source.id}") for index, source in enumerate(visible, start=1)]
        if page > 1:
            actions.append(ReplyAction("Back", f"/inbox {page - 1}"))
        if page < pages:
            actions.append(ReplyAction("Next", f"/inbox {page + 1}"))
        return PresentedReply("\n".join(lines), tuple(actions), title="Inbox", icon="📥")

    def file_card(self, source_id: int) -> PresentedReply | str:
        source = self._sources.get_by_id(source_id)
        if source is None:
            return "That file is no longer registered. Open /sources to choose another."
        sections = len(self._fragments.list_for_source(source_id))
        lines = [
            f"Location: {self.location(source)}",
            f"Type: {source.source_type.value}",
            f"Text: {sections} sections" if sections else "Text: none extracted",
        ]
        if source.status.value != "active":
            lines.append("Status: missing (moved or deleted since the last scan)")
        context = self._inbox_contexts.get(source_id) if self._inbox_contexts is not None else None
        if context is not None:
            if context.intended_root_name:
                lines.append(f"Intended root: {context.intended_root_name}")
            if context.user_context:
                lines.append(f"Note: {context.user_context}")
        actions = [
            ReplyAction("Read", f"/source_content {source_id}"),
            ReplyAction("Summarize", f"/summarize_source {source_id}"),
            ReplyAction("Ask", f"/ask_source {source_id}"),
        ]
        if self._export is not None:
            actions.append(ReplyAction("Send original", f"/send_source {source_id}"))
        folder = self._folder_command(source)
        if folder is not None:
            actions.append(ReplyAction("Folder", folder))
        return PresentedReply(
            "\n".join(lines), tuple(actions), title=source.path.name, icon="📄", reference=("source", source_id),
        )

    def read(self, source_id: int, section: int = 1) -> PresentedReply | str:
        source = self._sources.get_by_id(source_id)
        if source is None:
            return "That file is no longer registered. Open /sources to choose another."
        if source.status.value != "active":
            return "That file is missing. Rescan its folder on your computer, then try again."
        fragments = self._fragments.list_for_source(source_id)
        if not fragments:
            actions = [ReplyAction("File", f"/source {source_id}")]
            if self._export is not None:
                actions.append(ReplyAction("Send original", f"/send_source {source_id}"))
            return PresentedReply(
                "No text is stored for this file. That doesn't mean it's empty.\n\n"
                + _recovery_guidance(source)
                + "\n\nAfter fixing the cause, run `steward reextract SOURCE_ID` on your computer.",
                tuple(actions), title=source.path.name, icon="📄", reference=("source", source_id),
            )
        if not 1 <= section <= len(fragments):
            return f"Choose a section between 1 and {len(fragments)}."
        fragment = fragments[section - 1]
        actions = []
        if section > 1:
            actions.append(ReplyAction("Previous", f"/source_content {source_id} {section - 1}"))
        if section < len(fragments):
            actions.append(ReplyAction("Next", f"/source_content {source_id} {section + 1}"))
        actions.extend((
            ReplyAction("Summarize", f"/summarize_source {source_id}"),
            ReplyAction("Ask", f"/ask_source {source_id}"),
            ReplyAction("Back", f"/source {source_id}"),
        ))
        return PresentedReply(
            f"Section {section} of {len(fragments)} · {fragment.location}\n{fragment.heading or ''}\n\n{fragment.text}",
            tuple(actions), title=source.path.name, icon="📖", reference=("source", source_id),
        )

    def send_original(self, source_id: int) -> PresentedReply | str:
        if self._export is None:
            return "Sending originals is not configured on this computer."
        try:
            document = self._export.export(source_id)
        except ValueError as error:
            return str(error)
        except OSError:
            return "The original could not be read. Check that it is available on your computer."
        return PresentedReply("Here's the original.", title=document.filename, document=document)

    def summarize(self, source_id: int) -> PresentedReply | str:
        source = self._available(source_id)
        if isinstance(source, str):
            return source
        if self._answers is None:
            return "No model is configured. You can still Read the file."
        return self._answers.summarize(source)

    def ask_about(self, event: IncomingEvent, source_id: int, question: str) -> PresentedReply | str:
        source = self._available(source_id)
        if isinstance(source, str):
            return source
        if self._answers is None:
            return "No model is configured. You can still Read the file."
        return self._answers.ask(event.chat_id, question, source=source)

    def _available(self, source_id: int) -> Source | str:
        source = self._sources.get_by_id(source_id)
        if source is None or source.status.value != "active":
            return "That file is unavailable. Open /sources to choose another."
        return source

    # -- helpers ----------------------------------------------------------

    def location(self, source: Source) -> str:
        """Root- or Inbox-relative location; never an absolute path."""
        root = self._root_for(source)
        if root is not None:
            return " / ".join((root.name, *source.path.resolve().relative_to(root.path.resolve()).parts))
        if source.path.resolve().is_relative_to(self._inbox):
            return f"Inbox / {source.path.name}"
        return "Outside your folders"

    def _root_for(self, source: Source) -> SourceRoot | None:
        path = source.path.resolve()
        matches = [root for root in self._roots.list_all() if path.is_relative_to(root.path.resolve())]
        return max(matches, key=lambda root: len(root.path.parts), default=None)

    def _folder_command(self, source: Source) -> str | None:
        root = self._root_for(source)
        if root is not None:
            relative = source.path.resolve().relative_to(root.path.resolve())
            return _browse_command(root, PurePosixPath(*relative.parts[:-1]))
        if source.path.resolve().is_relative_to(self._inbox):
            return "/inbox"
        return None

    def _waiting_in_inbox(self) -> list[Source]:
        return sorted(
            (
                source for source in self._sources.list_active()
                if source.path.resolve().parent == self._inbox and source.path.is_file()
            ),
            key=lambda source: source.first_seen_at,
        )

    def _no_text_marker(self, source: Source) -> str:
        return "" if self._fragments.list_for_source(source.id or 0) else " · no text"

    def _remember(self, event: IncomingEvent, source_id: int, reply: PresentedReply | str) -> PresentedReply | str:
        if self._contexts is not None and isinstance(reply, PresentedReply):
            self._contexts.set(event.platform, event.chat_id, "source", source_id)
        return reply


def _browse_command(root: SourceRoot, folder: PurePosixPath, page: int = 1) -> str:
    suffix = f" {folder.as_posix()}" if folder.parts else ""
    return f"/browse {root.id} {page}{suffix}"


def _split_command(text: str | None) -> tuple[str, str]:
    parts = (text or "").strip().split(maxsplit=1)
    if not parts:
        return "", ""
    return parts[0].partition("@")[0].casefold(), parts[1].strip() if len(parts) > 1 else ""


def _page(argument: str) -> int:
    return max(1, int(argument)) if argument.isdigit() else 1
