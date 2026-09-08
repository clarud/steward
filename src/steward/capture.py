"""Preserve incoming material in the local Inbox before later interpretation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import logging
from pathlib import Path
import re
from shutil import copy2

from steward.events import IncomingEvent
from steward.extraction import DocumentExtractionError, ExtractionService, SourceFragmentRepository
from steward.sources import Source, SourceRepository, SourceType, source_type_for_path
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
    ) -> None:
        self._inbox_dir = inbox_dir
        self._source_repository = source_repository
        self._fragment_repository = fragment_repository
        self._extraction_service = (
            ExtractionService(fragment_repository) if fragment_repository is not None else None
        )
        self._activity_service = activity_service

    def capture_text(self, event: IncomingEvent) -> CaptureResult:
        """Preserve a text message as a human-readable Markdown original."""

        if not event.text or not event.text.strip():
            raise ValueError("A text capture requires non-empty text.")
        path = self._inbox_dir / f"{event.platform}-{event.chat_id}-{event.message_id}.md"
        existing = self._source_repository.get_by_path(path.resolve())
        if existing is not None:
            return CaptureResult(source=existing, duplicate=True)

        self._inbox_dir.mkdir(parents=True, exist_ok=True)
        path.write_text(event.text, encoding="utf-8")
        stat = path.stat()
        captured_at = datetime.now(UTC)
        source = self._source_repository.add(
            Source(
                id=None,
                path=path.resolve(),
                content_hash=hash_file(path),
                source_type=SourceType.MARKDOWN,
                size_bytes=stat.st_size,
                modified_at=datetime.fromtimestamp(stat.st_mtime, tz=UTC),
                first_seen_at=captured_at,
                last_seen_at=captured_at,
            )
        )
        self._extract_derived_content(source)
        self._record(source)
        return CaptureResult(source=source, duplicate=False)

    def capture_file(self, event: IncomingEvent, original_path: Path) -> CaptureResult:
        """Preserve one downloaded original without extracting or modifying it."""

        if not original_path.is_file():
            raise FileNotFoundError(original_path)
        suffix = original_path.suffix.casefold()
        source_type = source_type_for_path(original_path) or SourceType.BINARY
        original_name = event.attachments[0] if event.attachments else original_path.name
        safe_stem = re.sub(r"[^A-Za-z0-9._-]+", "-", Path(original_name).stem).strip(".-")
        destination = self._inbox_dir / (
            f"{event.platform}-{event.chat_id}-{event.message_id}-{safe_stem or 'attachment'}{suffix}"
        )
        existing = self._source_repository.get_by_path(destination.resolve())
        if existing is not None:
            return CaptureResult(source=existing, duplicate=True)

        self._inbox_dir.mkdir(parents=True, exist_ok=True)
        copy2(original_path, destination)
        stat = destination.stat()
        captured_at = datetime.now(UTC)
        source = self._source_repository.add(
            Source(
                id=None,
                path=destination.resolve(),
                content_hash=hash_file(destination),
                source_type=source_type,
                size_bytes=stat.st_size,
                modified_at=datetime.fromtimestamp(stat.st_mtime, tz=UTC),
                first_seen_at=captured_at,
                last_seen_at=captured_at,
            )
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

    def _record(self, source: Source) -> None:
        if self._activity_service is not None:
            self._activity_service.record(ActivityType.SOURCE_CAPTURED, object_id=str(source.id), details=str(source.path))
