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
from steward.workspaces import Workspace, WorkspaceRepository, WorkspaceService

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
    workspace_name: str | None = None
    user_guidance: str | None = None


@dataclass(frozen=True, slots=True)
class PendingOrganizationApproval:
    """The one proposal currently awaiting a decision in a transport chat."""

    platform: str
    chat_id: str
    proposal_id: int
    thread_id: str
    status: str

class OrganizationService:
    def propose(self, source: Source, workspaces: list[Workspace]) -> OrganizationProposal:
        matches = [w for w in workspaces if w.name.casefold() in source.path.name.casefold()]
        if matches:
            workspace = matches[0]
            vault_root = source.path.parent.parent if source.path.parent.name == "inbox" else source.path.parent
            return OrganizationProposal(None, source.id or 0, "move_to_workspace", workspace.id, vault_root / "projects" / workspace.name / source.path.name,
                f"The source filename matches workspace '{workspace.name}'.", 1.0)
        return OrganizationProposal(None, source.id or 0, "keep_in_inbox", None, None, "No reliable workspace match; keep this source in Inbox.", 0.0)

    def propose_with_context(
        self, source: Source, workspaces: list[Workspace], guidance: str
    ) -> OrganizationProposal:
        """Use explicit user wording to select only an existing workspace.

        Context may refine a proposal, but it never authorizes a path supplied
        by chat text and never creates a workspace implicitly.
        """

        normalized = " ".join(guidance.split()).casefold()
        matches = [workspace for workspace in workspaces if workspace.name.casefold() in normalized]
        if len(matches) != 1:
            return self.propose(source, workspaces)
        workspace = matches[0]
        vault_root = source.path.parent.parent if source.path.parent.name.casefold() == "inbox" else source.path.parent
        return OrganizationProposal(
            None,
            source.id or 0,
            "move_to_workspace",
            workspace.id,
            vault_root / "projects" / workspace.name / source.path.name,
            f"Your added context selected the existing workspace '{workspace.name}'.",
            1.0,
            user_guidance=" ".join(guidance.split())[:500],
        )


class OrganizationProposalRepository:
    def __init__(self, database_path: Path) -> None: self._database_path = database_path
    def add(self, proposal: OrganizationProposal) -> int:
        with sqlite3.connect(self._database_path) as connection:
            cursor = connection.execute(
                "INSERT INTO organization_proposals "
                "(source_id, workspace_id, suggested_path, rationale, score, status, created_at, proposal_type, confidence, workspace_name, user_guidance) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (proposal.source_id, proposal.workspace_id, str(proposal.suggested_path) if proposal.suggested_path else None,
                 proposal.rationale, proposal.confidence, proposal.status, datetime.now(UTC).isoformat(),
                 proposal.proposal_type, proposal.confidence, proposal.workspace_name, proposal.user_guidance),
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
                "SELECT id, source_id, proposal_type, workspace_id, suggested_path, rationale, confidence, status, workspace_name, user_guidance "
                "FROM organization_proposals ORDER BY id"
            ).fetchall()
        return [OrganizationProposal(int(r[0]), int(r[1]), str(r[2]), int(r[3]) if r[3] is not None else None,
                                     Path(str(r[4])) if r[4] else None, str(r[5]), float(r[6]), str(r[7]),
                                     str(r[8]) if r[8] else None, str(r[9]) if r[9] else None) for r in rows]
    def get(self, proposal_id: int) -> OrganizationProposal | None:
        return next((proposal for proposal in self.list_all() if proposal.id == proposal_id), None)


class OrganizationApprovalThreadRepository:
    """Persist the chat-to-interrupted-graph mapping across process restarts."""

    def __init__(self, database_path: Path) -> None:
        self._database_path = database_path

    def get_pending(self, platform: str, chat_id: str) -> PendingOrganizationApproval | None:
        with sqlite3.connect(self._database_path) as connection:
            row = connection.execute(
                "SELECT platform, chat_id, proposal_id, thread_id, status "
                "FROM organization_approval_threads "
                "WHERE platform = ? AND chat_id = ? AND status = 'pending'",
                (platform, chat_id),
            ).fetchone()
        return self._from_row(row) if row is not None else None

    def start(self, platform: str, chat_id: str, proposal_id: int, thread_id: str) -> None:
        if self.get_pending(platform, chat_id) is not None:
            raise ValueError("This chat already has an organization proposal awaiting review.")
        with sqlite3.connect(self._database_path) as connection:
            created_at = datetime.now(UTC).isoformat()
            cursor = connection.execute(
                "UPDATE organization_approval_threads SET proposal_id = ?, thread_id = ?, status = 'pending', created_at = ? "
                "WHERE platform = ? AND chat_id = ? AND status != 'pending'",
                (proposal_id, thread_id, created_at, platform, chat_id),
            )
            if cursor.rowcount == 0:
                connection.execute(
                    "INSERT INTO organization_approval_threads "
                    "(platform, chat_id, proposal_id, thread_id, status, created_at) "
                    "VALUES (?, ?, ?, ?, 'pending', ?)",
                    (platform, chat_id, proposal_id, thread_id, created_at),
                )

    def finish(self, platform: str, chat_id: str, status: str) -> None:
        if status not in {"accepted", "rejected"}:
            raise ValueError("Organization approval status must be accepted or rejected.")
        with sqlite3.connect(self._database_path) as connection:
            cursor = connection.execute(
                "UPDATE organization_approval_threads SET status = ? "
                "WHERE platform = ? AND chat_id = ? AND status = 'pending'",
                (status, platform, chat_id),
            )
        if cursor.rowcount != 1:
            raise ValueError("No pending organization approval was found for this chat.")

    @staticmethod
    def _from_row(row: tuple[object, ...]) -> PendingOrganizationApproval:
        return PendingOrganizationApproval(
            platform=str(row[0]),
            chat_id=str(row[1]),
            proposal_id=int(row[2]),
            thread_id=str(row[3]),
            status=str(row[4]),
        )


class OrganizationApprovalService:
    """Apply one reviewed proposal through the same safe path in every adapter."""

    def __init__(
        self,
        proposal_repository: OrganizationProposalRepository,
        source_repository: SourceRepository,
        file_mutation_service: FileMutationService,
        activity_service: ActivityService,
        workspace_repository: WorkspaceRepository | None = None,
    ) -> None:
        self._proposals = proposal_repository
        self._sources = source_repository
        self._files = file_mutation_service
        self._activity = activity_service
        self._workspaces = workspace_repository

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
            if proposal.proposal_type == "create_workspace_and_move":
                if self._workspaces is None or not proposal.workspace_name:
                    raise ValueError("New-workspace organization proposals are not configured for this Steward process.")
                existing = next(
                    (workspace for workspace in self._workspaces.list_all()
                     if workspace.name.casefold() == proposal.workspace_name.casefold()),
                    None,
                )
                if existing is None:
                    WorkspaceService(self._workspaces, self._activity).create(proposal.workspace_name)
            source = self._sources.get_by_id(proposal.source_id)
            if source is None:
                raise ValueError(f"Source {proposal.source_id} was not found.")
            # A restart may re-enter this node after a successful filesystem move
            # but before its status was saved. The registered destination makes
            # that recovery path a no-op instead of a second move.
            if source.path.resolve() != proposal.suggested_path.resolve():
                self._files.move_source(source.path, proposal.suggested_path)
            if proposal.workspace_id is not None and self._workspaces is not None:
                # Physical placement and semantic relevance answer different
                # questions. This idempotent link is retried after an
                # interrupted filesystem move before the proposal can finish.
                if self._workspaces.link_source(proposal.workspace_id, proposal.source_id):
                    self._activity.record(
                        ActivityType.SOURCE_LINKED_TO_WORKSPACE,
                        object_id=str(proposal.source_id),
                        details=f"workspace:{proposal.workspace_id}",
                    )

        self._proposals.set_status(proposal_id, decision)
        self._activity.record(
            ActivityType.ORGANIZATION_ACCEPTED if decision == "accepted" else ActivityType.ORGANIZATION_REJECTED,
            object_id=str(proposal_id),
        )
        reviewed = self._proposals.get(proposal_id)
        if reviewed is None:
            raise RuntimeError("Reviewed organization proposal disappeared.")
        return reviewed
