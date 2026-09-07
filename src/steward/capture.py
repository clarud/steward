"""Preserve incoming material in the local Inbox before later interpretation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from shutil import copy2

from steward.events import IncomingEvent
from steward.extraction import MarkdownExtractor, SourceFragmentRepository
from steward.sources import Source, SourceRepository, SourceType
from steward.sources.hashing import hash_file


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
    ) -> None:
        self._inbox_dir = inbox_dir
        self._source_repository = source_repository
        self._fragment_repository = fragment_repository
        self._markdown_extractor = MarkdownExtractor()

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
        self._extract_markdown(source)
        return CaptureResult(source=source, duplicate=False)

    def capture_file(self, event: IncomingEvent, original_path: Path) -> CaptureResult:
        """Preserve one downloaded original without extracting or modifying it."""

        if not original_path.is_file():
            raise FileNotFoundError(original_path)
        suffix = original_path.suffix.casefold()
        source_type = {
            ".md": SourceType.MARKDOWN,
            ".pdf": SourceType.PDF,
        }.get(suffix, SourceType.BINARY)
        destination = self._inbox_dir / (
            f"{event.platform}-{event.chat_id}-{event.message_id}{suffix}"
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
        self._extract_markdown(source)
        return CaptureResult(source=source, duplicate=False)

    def _extract_markdown(self, source: Source) -> None:
        if self._fragment_repository is not None and source.source_type is SourceType.MARKDOWN:
            self._fragment_repository.replace_for_source(
                self._markdown_extractor.extract(source)
            )
