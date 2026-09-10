"""Explicit, bounded export of a registered original for user-requested delivery."""
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

from steward.roots import SourceRootRepository
from steward.sources.repository import SourceRepository


@dataclass(frozen=True, slots=True)
class OriginalDocument:
    filename: str
    content: bytes


class SourceExportService:
    MAX_BYTES = 20 * 1024 * 1024

    def __init__(self, sources: SourceRepository, roots: SourceRootRepository, inbox: Path) -> None:
        self._sources, self._roots, self._inbox = sources, roots, inbox.resolve()

    def export(self, source_id: int) -> OriginalDocument:
        source = self._sources.get_by_id(source_id)
        if source is None or source.status.value != "active":
            raise ValueError("This original is not currently available.")
        path = source.path.resolve()
        roots = self._roots.list_all()
        if any(path.is_relative_to((root.path / exclusion).resolve()) for root in roots for exclusion in root.exclusions):
            raise ValueError("This original is excluded from file delivery.")
        allowed = path.is_relative_to(self._inbox) or any(
            root.enabled and path.is_relative_to(root.path.resolve()) for root in roots
        )
        if not allowed:
            raise ValueError("This original is outside the currently authorized delivery roots.")
        if not path.is_file():
            raise ValueError("The original file is missing. Restore it locally and rescan.")
        with path.open("rb") as original:
            content = original.read(self.MAX_BYTES + 1)
        if len(content) > self.MAX_BYTES:
            raise ValueError("This original exceeds Steward's 20 MiB delivery limit. Open it locally instead.")
        if sha256(content).hexdigest() != source.content_hash:
            raise ValueError("The original changed since its last scan. Rescan locally before sending it.")
        return OriginalDocument(path.name, content)
