"""Metadata-only Codex handoff manifests for selected sources."""

from __future__ import annotations

from pathlib import Path

from steward.events import IncomingEvent
from steward.presentation import PresentedReply, ReplyAction
from steward.sources import CodexHandoffService, SourceRepository


class StewardCodexHandoffApplication:
    """Prepare a local metadata-only handoff from explicitly selected IDs."""

    _PAGE_SIZE = 8

    def __init__(
        self,
        handoffs: CodexHandoffService,
        sources: SourceRepository | None = None,
        inbox_dir: Path | None = None,
    ) -> None:
        self._handoffs = handoffs
        self._sources = sources
        self._inbox_dir = inbox_dir.resolve() if inbox_dir is not None else None

    def handle_command(self, event: IncomingEvent) -> str | PresentedReply | None:
        command, _, argument = (event.text or "").strip().partition(" ")
        command = command.partition("@")[0]
        if command == "/codex_handoff_page":
            try:
                return self._inbox_picker(int(argument or "1"))
            except ValueError:
                return "Use /codex_handoff_page followed by a page number."
        if command != "/codex_handoff":
            return None
        parts = argument.split()
        if not parts:
            return self._inbox_picker(1)
        if not all(item.isdigit() for item in parts):
            return "Use /codex_handoff followed by one or more source IDs. Source content will not be sent."
        try:
            handoff = self._handoffs.prepare(tuple(int(item) for item in parts))
        except ValueError as error:
            return f"Codex handoff was not prepared: {error}"
        return PresentedReply(
            f"Prepared local handoff {handoff.identifier} for source IDs: {', '.join(map(str, handoff.source_ids))}.\n\n"
            "It contains metadata and guidance paths only. No source content was sent, no Codex session was invoked, and no files changed. Open the manifest locally before explicitly sharing it with Codex.",
            (ReplyAction("Browse sources", "/sources"), ReplyAction("Home", "/home")),
            title="Codex handoff ready", icon="📋",
        )

    @staticmethod
    def _root_detail(root: object) -> PresentedReply:
        """Render health-only root information shared by commands and follow-ups."""

        health = str(getattr(root, "health"))
        guidance = (
            "Reconnect or restore this root locally, then scan it locally."
            if health == "missing"
            else "Enable this root locally before scanning."
            if health == "disabled"
            else "This root is available for local scans."
        )
        identifier = getattr(root, "id")
        last_scanned_at = getattr(root, "last_scanned_at", None)
        scan_status = last_scanned_at.isoformat() if last_scanned_at is not None else "never"
        return PresentedReply(
            f"Status: {health}\nLast successful scan: {scan_status}\nExcluded subdirectories: {len(getattr(root, 'exclusions'))}\n\n"
            f"{guidance}\nRoot paths and changes remain local-only.",
            (ReplyAction("Roots", "/roots"), ReplyAction("Home", "/home")),
            title=str(getattr(root, "name")), icon="🗂️",
            reference=("root", identifier) if isinstance(identifier, int) and identifier > 0 else None,
        )


    def _inbox_picker(self, page: int) -> str | PresentedReply:
        """Offer explicit one-source handoffs for active Inbox material only."""
        if self._sources is None or self._inbox_dir is None:
            return "Use /codex_handoff followed by one or more source IDs. Source content will not be sent."
        sources = [
            source for source in self._sources.list_active()
            if source.path.resolve().is_relative_to(self._inbox_dir)
        ]
        if not sources:
            return "Your Inbox has no active sources to prepare for Codex."
        pages = max(1, (len(sources) + self._PAGE_SIZE - 1) // self._PAGE_SIZE)
        page = max(1, min(page, pages))
        visible = sources[(page - 1) * self._PAGE_SIZE:page * self._PAGE_SIZE]
        lines = [
            "Choose one Inbox source to prepare a local metadata-only handoff.",
            "This does not send content to Codex or move any file.",
            f"Page {page} of {pages}",
            "",
        ]
        lines.extend(f"{source.id}. {source.path.name} · {source.source_type.value}" for source in visible)
        actions: list[ReplyAction] = [
            ReplyAction(f"Prepare #{source.id}", f"/codex_handoff {source.id}")
            for source in visible if source.id is not None
        ]
        if page > 1:
            actions.append(ReplyAction("Previous", f"/codex_handoff_page {page - 1}"))
        if page < pages:
            actions.append(ReplyAction("Next", f"/codex_handoff_page {page + 1}"))
        actions.append(ReplyAction("Inbox", "/inbox"))
        return PresentedReply(
            "\n".join(lines), tuple(actions), title="Prepare Codex handoff", icon="📋",
        )
