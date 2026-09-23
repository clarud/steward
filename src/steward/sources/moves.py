"""Keep a file's identity when Codex (or anything else) renames or moves it."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, replace
from pathlib import Path

from steward.sources.models import Source, SourceStatus
from steward.sources.repository import SourceRepository


@dataclass(frozen=True, slots=True)
class ReconciledMove:
    source: Source
    previous_path: Path


class MoveReconciler:
    """Merge a vanished file with its reappearance elsewhere, when that is certain.

    A move is recognised only when exactly one missing file and exactly one
    present file share a content hash, and the present file was first seen
    after the missing one was last seen. Anything ambiguous (copies, edits
    during the move) is left as separate files, which stay searchable.
    Nothing on disk is touched.
    """

    def __init__(self, sources: SourceRepository, inbox_dir: Path | None = None) -> None:
        self._sources = sources
        self._inbox = inbox_dir.resolve() if inbox_dir is not None else None

    def reconcile(self) -> tuple[ReconciledMove, ...]:
        self._mark_filed_inbox_items_missing()
        missing: dict[str, list[Source]] = defaultdict(list)
        present: dict[str, list[Source]] = defaultdict(list)
        for source in self._sources.list_all():
            (missing if source.status is SourceStatus.MISSING else present)[source.content_hash].append(source)
        moves = []
        for content_hash, old_matches in missing.items():
            new_matches = present.get(content_hash, [])
            if len(old_matches) != 1 or len(new_matches) != 1:
                continue
            old, new = old_matches[0], new_matches[0]
            if old.id is None or new.id is None or new.first_seen_at < old.last_seen_at:
                continue
            moves.append(ReconciledMove(self._sources.replace_discovered_move(old.id, new.id), old.path))
        return tuple(moves)

    def _mark_filed_inbox_items_missing(self) -> None:
        """The Inbox is never scanned, so notice here when a file has left it."""
        if self._inbox is None:
            return
        for source in self._sources.list_active():
            if source.path.resolve().parent == self._inbox and not source.path.exists():
                self._sources.update(replace(source, status=SourceStatus.MISSING))
