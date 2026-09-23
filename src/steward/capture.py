"""Preserve incoming material in the local Inbox before later interpretation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import logging
from pathlib import Path
import re
import sqlite3
from shutil import copy2

from steward.events import IncomingEvent
from steward.extraction import DocumentExtractionError, ExtractionService, SourceFragmentRepository
from steward.sources import InboxQueue, Source, SourceRepository, SourceType, source_type_for_path
from steward.sources.inbox_queue import QUEUE_FILENAME
from steward.sources.hashing import hash_file
from steward.activity import ActivityService, ActivityType


logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class CaptureResult:
    """The preserved source and whether this event was already captured."""

    source: Source
    duplicate: bool


class InboxCaptureService:
    """Write original incoming text into Inbox and register it idempotently."""

    def __init__(
        self,
        inbox_dir: Path,
        source_repository: SourceRepository,
        fragment_repository: SourceFragmentRepository | None = None,
        activity_service: ActivityService | None = None,
        inbox_queue: InboxQueue | None = None,
    ) -> None:
        self._inbox_dir = inbox_dir
        self._inbox_queue = inbox_queue
        self._source_repository = source_repository
        self._fragment_repository = fragment_repository
        self._extraction_service = (
            ExtractionService(fragment_repository) if fragment_repository is not None else None
        )
        self._activity_service = activity_service

    def capture_text(self, event: IncomingEvent) -> CaptureResult:
        """Save a note as `YYYY-MM-DD first words.md` in the Inbox."""

        if not event.text or not event.text.strip():
            raise ValueError("A text capture requires non-empty text.")
        existing = self._existing(event, legacy_names=(f"{_legacy_prefix(event)}.md",))
        if existing is not None:
            return CaptureResult(source=existing, duplicate=True)
        title = _title(event.text) or "note"
        destination = self._unique_path(f"{event.timestamp.astimezone():%Y-%m-%d} {title}.md")
        destination.write_text(event.text, encoding="utf-8")
        return self._register(event, destination, SourceType.MARKDOWN)

    def capture_file(self, event: IncomingEvent, original_path: Path) -> CaptureResult:
        """Save an uploaded file under its original name, adding ` (2)` on a clash."""

        if not original_path.is_file():
            raise FileNotFoundError(original_path)
        original_name = event.attachments[0] if event.attachments else original_path.name
        existing = self._existing(event, legacy_glob=f"{_legacy_prefix(event)}-*")
        if existing is not None:
            return CaptureResult(source=existing, duplicate=True)
        suffix = Path(original_name).suffix or original_path.suffix
        stem = " ".join(_safe_name(Path(original_name).stem).split()).strip(" .") or "upload"
        destination = self._unique_path(f"{stem}{suffix.casefold()}")
        copy2(original_path, destination)
        return self._register(event, destination, source_type_for_path(destination) or SourceType.BINARY)

    def _existing(
        self, event: IncomingEvent, *, legacy_names: tuple[str, ...] = (), legacy_glob: str | None = None,
    ) -> Source | None:
        """Find a previous capture of this exact message, including pre-rename captures."""
        with sqlite3.connect(self._source_repository.database_path) as connection:
            row = connection.execute(
                "SELECT source_id FROM inbox_captures WHERE capture_key = ?", (_capture_key(event),)
            ).fetchone()
        if row is not None:
            found = self._source_repository.get_by_id(int(row[0]))
            if found is not None:
                return found
        candidates = [self._inbox_dir / name for name in legacy_names]
        if legacy_glob is not None and self._inbox_dir.is_dir():
            candidates.extend(sorted(self._inbox_dir.glob(legacy_glob)))
        for candidate in candidates:
            found = self._source_repository.get_by_path(candidate.resolve())
            if found is not None:
                return found
        return None

    def _unique_path(self, filename: str) -> Path:
        self._inbox_dir.mkdir(parents=True, exist_ok=True)
        candidate = self._inbox_dir / filename
        stem, suffix = Path(filename).stem, Path(filename).suffix
        counter = 2
        while candidate.exists() or candidate.name.casefold() == QUEUE_FILENAME.casefold():
            candidate = self._inbox_dir / f"{stem} ({counter}){suffix}"
            counter += 1
        return candidate

    def _register(self, event: IncomingEvent, path: Path, source_type: SourceType) -> CaptureResult:
        stat = path.stat()
        captured_at = datetime.now(UTC)
        source = self._source_repository.add(
            Source(
                id=None,
                path=path.resolve(),
                content_hash=hash_file(path),
                source_type=source_type,
                size_bytes=stat.st_size,
                modified_at=datetime.fromtimestamp(stat.st_mtime, tz=UTC),
                first_seen_at=captured_at,
                last_seen_at=captured_at,
            )
        )
        with sqlite3.connect(self._source_repository.database_path) as connection:
            connection.execute(
                "INSERT OR IGNORE INTO inbox_captures (capture_key, source_id, created_at) VALUES (?, ?, ?)",
                (_capture_key(event), source.id, captured_at.isoformat()),
            )
        self._extract_derived_content(source)
        self._record(source)
        return CaptureResult(source=source, duplicate=False)

    def _extract_derived_content(self, source: Source) -> None:
        if self._extraction_service is not None:
            try:
                self._extraction_service.extract_and_store(source)
            except (OSError, UnicodeDecodeError, DocumentExtractionError) as error:
                logger.warning("Captured %s but could not extract text: %s", source.path, error)

    def refresh_queue(self) -> None:
        """Rewrite INBOX.md so the Steward computer sees what is waiting to be filed."""
        if self._inbox_queue is None:
            return
        try:
            self._inbox_queue.refresh()
        except OSError as error:
            # The capture itself succeeded; a stale list must not undo it.
            logger.warning("Could not update the Inbox queue: %s", error)

    def _record(self, source: Source) -> None:
        self.refresh_queue()
        if self._activity_service is not None:
            self._activity_service.record(ActivityType.SOURCE_CAPTURED, object_id=str(source.id), details=str(source.path))


def _capture_key(event: IncomingEvent) -> str:
    return f"{event.platform}:{event.chat_id}:{event.message_id}"


def _legacy_prefix(event: IncomingEvent) -> str:
    """Filenames used before readable names, so old captures still count as duplicates."""
    return f"{event.platform}-{event.chat_id}-{event.message_id}"


def _safe_name(value: str) -> str:
    """Keep a readable filename that is valid on Windows, macOS, and Linux."""
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]+', " ", value)


def _title(text: str, *, max_words: int = 8, max_length: int = 60) -> str:
    """The note's first words, cut at a word boundary, safe as a filename."""
    title = ""
    for word in _safe_name(text).split()[:max_words]:
        if len(title) + len(word) + 1 > max_length:
            break
        title = f"{title} {word}".strip()
    return title.rstrip(" .")
