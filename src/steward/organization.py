"""Proposal-only source organization; no filesystem mutation occurs here."""
from __future__ import annotations
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
import sqlite3
from steward.sources import Source
from steward.workspaces import Workspace

@dataclass(frozen=True, slots=True)
class OrganizationProposal:
    id: int | None
    source_id: int
    workspace_id: int | None
    suggested_path: Path | None
    rationale: str
    score: float
    status: str = "pending"

class OrganizationService:
    def propose(self, source: Source, workspaces: list[Workspace]) -> OrganizationProposal:
        matches = [w for w in workspaces if w.name.casefold() in source.path.name.casefold()]
        if matches:
            workspace = matches[0]
            return OrganizationProposal(None, source.id or 0, workspace.id, Path("projects") / workspace.name / source.path.name,
                f"The source filename matches workspace '{workspace.name}'.", 1.0)
        return OrganizationProposal(None, source.id or 0, None, None, "No reliable workspace match; keep this source in Inbox.", 0.0)


class OrganizationProposalRepository:
    def __init__(self, database_path: Path) -> None: self._database_path = database_path
    def add(self, proposal: OrganizationProposal) -> int:
        with sqlite3.connect(self._database_path) as connection:
            cursor = connection.execute("INSERT INTO organization_proposals (source_id,workspace_id,suggested_path,rationale,score,status,created_at) VALUES (?,?,?,?,?,?,?)", (proposal.source_id, proposal.workspace_id, str(proposal.suggested_path) if proposal.suggested_path else None, proposal.rationale, proposal.score, proposal.status, datetime.now(UTC).isoformat()))
        return int(cursor.lastrowid)
    def set_status(self, proposal_id: int, status: str) -> None:
        if status not in {"accepted", "rejected"}: raise ValueError("Proposal status must be accepted or rejected.")
        with sqlite3.connect(self._database_path) as connection:
            cursor = connection.execute("UPDATE organization_proposals SET status=? WHERE id=?", (status, proposal_id))
        if cursor.rowcount != 1: raise ValueError("Organization proposal was not found.")

    def list_all(self) -> list[OrganizationProposal]:
        with sqlite3.connect(self._database_path) as connection:
            rows = connection.execute("SELECT id,source_id,workspace_id,suggested_path,rationale,score,status FROM organization_proposals ORDER BY id").fetchall()
        return [OrganizationProposal(int(r[0]), int(r[1]), int(r[2]) if r[2] is not None else None, Path(str(r[3])) if r[3] else None, str(r[4]), float(r[5]), str(r[6])) for r in rows]
