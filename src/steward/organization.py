"""Proposal-only source organization; no filesystem mutation occurs here."""
from __future__ import annotations
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
import sqlite3
from steward.actions import FileMutationService
from steward.activity import ActivityService, ActivityType
from steward.sources import Source
from steward.sources import SourceRepository
from steward.workspaces import Workspace

@dataclass(frozen=True, slots=True)
class OrganizationProposal:
    id: int | None
    source_id: int
    proposal_type: str
    workspace_id: int | None
    suggested_path: Path | None
    rationale: str
    confidence: float
    status: str = "pending"

class OrganizationService:
    def propose(self, source: Source, workspaces: list[Workspace]) -> OrganizationProposal:
        matches = [w for w in workspaces if w.name.casefold() in source.path.name.casefold()]
        if matches:
            workspace = matches[0]
            vault_root = source.path.parent.parent if source.path.parent.name == "inbox" else source.path.parent
            return OrganizationProposal(None, source.id or 0, "move_to_workspace", workspace.id, vault_root / "projects" / workspace.name / source.path.name,
                f"The source filename matches workspace '{workspace.name}'.", 1.0)
        return OrganizationProposal(None, source.id or 0, "keep_in_inbox", None, None, "No reliable workspace match; keep this source in Inbox.", 0.0)


class OrganizationProposalRepository:
    def __init__(self, database_path: Path) -> None: self._database_path = database_path
    def add(self, proposal: OrganizationProposal) -> int:
        with sqlite3.connect(self._database_path) as connection:
            cursor = connection.execute(
                "INSERT INTO organization_proposals "
                "(source_id, workspace_id, suggested_path, rationale, score, status, created_at, proposal_type, confidence) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (proposal.source_id, proposal.workspace_id, str(proposal.suggested_path) if proposal.suggested_path else None,
                 proposal.rationale, proposal.confidence, proposal.status, datetime.now(UTC).isoformat(),
                 proposal.proposal_type, proposal.confidence),
            )
        return int(cursor.lastrowid)
    def set_status(self, proposal_id: int, status: str) -> None:
        if status not in {"accepted", "rejected"}: raise ValueError("Proposal status must be accepted or rejected.")
        with sqlite3.connect(self._database_path) as connection:
            cursor = connection.execute("UPDATE organization_proposals SET status=? WHERE id=?", (status, proposal_id))
        if cursor.rowcount != 1: raise ValueError("Organization proposal was not found.")

    def list_all(self) -> list[OrganizationProposal]:
        with sqlite3.connect(self._database_path) as connection:
            rows = connection.execute(
                "SELECT id, source_id, proposal_type, workspace_id, suggested_path, rationale, confidence, status "
                "FROM organization_proposals ORDER BY id"
            ).fetchall()
        return [OrganizationProposal(int(r[0]), int(r[1]), str(r[2]), int(r[3]) if r[3] is not None else None,
                                     Path(str(r[4])) if r[4] else None, str(r[5]), float(r[6]), str(r[7])) for r in rows]
    def get(self, proposal_id: int) -> OrganizationProposal | None:
        return next((proposal for proposal in self.list_all() if proposal.id == proposal_id), None)


class OrganizationApprovalService:
    """Apply one reviewed proposal through the same safe path in every adapter."""

    def __init__(
        self,
        proposal_repository: OrganizationProposalRepository,
        source_repository: SourceRepository,
        file_mutation_service: FileMutationService,
        activity_service: ActivityService,
    ) -> None:
        self._proposals = proposal_repository
        self._sources = source_repository
        self._files = file_mutation_service
        self._activity = activity_service

    def review(self, proposal_id: int, decision: str) -> OrganizationProposal:
        if decision not in {"accepted", "rejected"}:
            raise ValueError("Proposal decision must be accepted or rejected.")
        proposal = self._proposals.get(proposal_id)
        if proposal is None:
            raise ValueError("Organization proposal was not found.")
        if proposal.status == decision:
            return proposal
        if proposal.status != "pending":
            raise ValueError(f"Proposal {proposal_id} was already {proposal.status}.")

        if decision == "accepted" and proposal.suggested_path is not None:
            source = self._sources.get_by_id(proposal.source_id)
            if source is None:
                raise ValueError(f"Source {proposal.source_id} was not found.")
            # A restart may re-enter this node after a successful filesystem move
            # but before its status was saved. The registered destination makes
            # that recovery path a no-op instead of a second move.
            if source.path.resolve() != proposal.suggested_path.resolve():
                self._files.move_source(source.path, proposal.suggested_path)

        self._proposals.set_status(proposal_id, decision)
        self._activity.record(
            ActivityType.ORGANIZATION_ACCEPTED if decision == "accepted" else ActivityType.ORGANIZATION_REJECTED,
            object_id=str(proposal_id),
        )
        reviewed = self._proposals.get(proposal_id)
        if reviewed is None:
            raise RuntimeError("Reviewed organization proposal disappeared.")
        return reviewed
