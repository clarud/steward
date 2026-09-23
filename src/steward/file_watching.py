"""Debounced filesystem notifications backed by authoritative content hashes."""

from __future__ import annotations

from pathlib import Path
from time import monotonic
from time import sleep

from steward.sources.service import SourceService
from steward.sources.discovery import DEFAULT_EXCLUDED_DIRECTORY_NAMES


class FileWatchService:
    def __init__(self, root: Path, source_service: SourceService, *, debounce_seconds: float = 0.5, exclusions: tuple[Path, ...] = ()) -> None:
        if debounce_seconds <= 0:
            raise ValueError("debounce_seconds must be positive.")
        self._root = root.resolve()
        self._exclusions = tuple((item if item.is_absolute() else self._root / item).resolve() for item in exclusions)
        self._source_service = source_service
        self._debounce_seconds = debounce_seconds
        self._pending: dict[Path, float] = {}

    def notify(self, path: Path, *, observed_at: float | None = None) -> None:
        resolved = path.resolve()
        if (
            resolved.is_relative_to(self._root)
            and not any(resolved.is_relative_to(exclusion) for exclusion in self._exclusions)
            and not any(parent.name in DEFAULT_EXCLUDED_DIRECTORY_NAMES for parent in resolved.parents)
        ):
            self._pending[resolved] = monotonic() if observed_at is None else observed_at

    def flush(self, *, now: float | None = None) -> dict[Path, str]:
        current = monotonic() if now is None else now
        ready = [path for path, observed in self._pending.items() if current - observed >= self._debounce_seconds]
        results = {path: self._source_service.refresh_source_path(path) for path in ready}
        for path in ready:
            del self._pending[path]
        return results


def run_file_watcher(root: Path, source_service: SourceService, *, debounce_seconds: float = 0.5, exclusions: tuple[Path, ...] = ()) -> None:
    """Run a foreground watchdog observer until interrupted by the user."""
    from watchdog.events import FileSystemEventHandler
    from watchdog.observers import Observer

    watcher = FileWatchService(root, source_service, debounce_seconds=debounce_seconds, exclusions=exclusions)

    class Handler(FileSystemEventHandler):
        def on_any_event(self, event) -> None:
            if not event.is_directory:
                watcher.notify(Path(event.src_path))
                destination = getattr(event, "dest_path", None)
                if destination:
                    watcher.notify(Path(destination))

    observer = Observer()
    observer.schedule(Handler(), str(root), recursive=True)
    observer.start()
    try:
        while True:
            watcher.flush()
            sleep(0.1)
    except KeyboardInterrupt:
        pass
    finally:
        observer.stop()
        observer.join()
