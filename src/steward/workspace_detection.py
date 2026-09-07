"""Explicit, reviewable discovery of possible new workspaces from Inbox sources."""

from __future__ import annotations

from dataclasses import dataclass
import re

from steward.sources import Source
from steward.workspaces import Workspace


@dataclass(frozen=True, slots=True)
class WorkspaceProposal:
    proposed_name: str
    source_ids: tuple[int, ...]
    rationale: str
    confidence: float
    status: str = "pending"


class WorkspaceDetectionService:
    """Cluster Inbox filenames conservatively; never creates a workspace itself."""

    def propose(self, sources: list[Source], workspaces: list[Workspace]) -> tuple[WorkspaceProposal, ...]:
        existing = {workspace.name.casefold() for workspace in workspaces}
        groups: dict[str, list[int]] = {}
        for source in sources:
            if source.id is None or source.path.parent.name.casefold() != "inbox":
                continue
            for token in self._tokens(source.path.stem):
                if token in existing:
                    continue
                groups.setdefault(token, []).append(source.id)
        return tuple(
            WorkspaceProposal(
                proposed_name=token.replace("-", " ").title(),
                source_ids=tuple(sorted(ids)),
                rationale=f"{len(ids)} Inbox sources share the filename term '{token}'.",
                confidence=min(0.9, 0.4 + 0.15 * len(ids)),
            )
            for token, ids in sorted(groups.items())
            if len(ids) >= 2
        )

    @staticmethod
    def _tokens(stem: str) -> set[str]:
        return {
            token.casefold()
            for token in re.findall(r"[A-Za-z][A-Za-z0-9]{2,}", stem)
            if token.casefold() not in {"notes", "note", "document", "file", "copy", "final"}
        }
