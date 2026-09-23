"""Local-only, reviewable manifests for a bounded Codex handoff."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

from steward.sources.repository import SourceRepository

if TYPE_CHECKING:
    from steward.roots import SourceRootRepository


@dataclass(frozen=True, slots=True)
class CodexHandoff:
    identifier: str
    path: Path
    source_ids: tuple[int, ...]


class CodexHandoffService:
    """Write metadata-only local handoff manifests; never invoke Codex."""

    def __init__(self, sources: SourceRepository, roots: "SourceRootRepository", data_dir: Path, inbox: Path) -> None:
        self._sources = sources
        self._roots = roots
        self._handoffs = data_dir.resolve() / "handoffs"
        self._inbox = inbox.resolve()

    def prepare(self, source_ids: tuple[int, ...], *, note: str = "") -> CodexHandoff:
        if not source_ids or len(set(source_ids)) != len(source_ids):
            raise ValueError("Choose one or more distinct source IDs for a Codex handoff.")
        roots = self._roots.list_all()
        selected = []
        guidance: set[str] = set()
        for source_id in source_ids:
            source = self._sources.get_by_id(source_id)
            if source is None or source.status.value != "active":
                raise ValueError(f"Source {source_id} is not currently available.")
            path = source.path.resolve()
            root = next((item for item in roots if path.is_relative_to(item.path.resolve())), None)
            if root is None and not path.is_relative_to(self._inbox):
                raise ValueError(f"Source {source_id} is outside authorized roots and Inbox.")
            root_name = root.name if root is not None else "Inbox"
            relative_path = str(path.relative_to(root.path.resolve())) if root is not None else str(path.relative_to(self._inbox))
            selected.append({
                "source_id": source.id, "filename": path.name, "path": str(path),
                "root": root_name, "root_relative_path": relative_path,
                "source_type": source.source_type.value, "content_hash": source.content_hash,
                "modified_at": source.modified_at.isoformat(),
            })
            if root is not None:
                for name in ("AGENTS.md", "COURSE_WORKFLOWS.md"):
                    candidate = root.path / name
                    if candidate.is_file():
                        guidance.add(str(candidate))
        identifier = f"handoff-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}-{uuid4().hex[:8]}"
        self._handoffs.mkdir(parents=True, exist_ok=True)
        path = self._handoffs / f"{identifier}.json"
        payload = {
            "handoff_id": identifier,
            "created_at": datetime.now(UTC).isoformat(),
            "purpose": note.strip(),
            "sources": selected,
            "guidance_document_paths": sorted(guidance),
            "safety": "Metadata only. Steward did not send source content, invoke Codex, or modify files.",
            "next_step": "Review this manifest locally, then explicitly provide it to Codex if desired. Run scan-root and review moves after filesystem changes.",
        }
        path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        return CodexHandoff(identifier, path, tuple(source_ids))
