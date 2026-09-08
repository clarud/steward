"""Durable, reviewable agent proposals for actions that would change state."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime

from steward.activity import ActivityService, ActivityType
from steward.workspaces import Workspace, WorkspaceRepository, WorkspaceService


@dataclass(frozen=True, slots=True)
class ActionProposal:
    """A requested action that has not been authorized for execution yet."""

    id: int | None
    action_type: str
    payload: dict[str, str]
    status: str
    created_at: datetime
    reviewed_at: datetime | None = None


class ActionProposalRepository:
    """SQLite persistence for generic action proposals."""

    def __init__(self, database_path) -> None:
        self._database_path = database_path

    def add(self, action_type: str, payload: dict[str, str]) -> ActionProposal:
        created_at = datetime.now(UTC)
        payload_json = json.dumps(payload, sort_keys=True)
        with sqlite3.connect(self._database_path) as connection:
            cursor = connection.execute(
                "INSERT INTO action_proposals (action_type, payload_json, status, created_at) "
                "VALUES (?, ?, 'pending', ?)",
                (action_type, payload_json, created_at.isoformat()),
            )
        return ActionProposal(int(cursor.lastrowid), action_type, payload, "pending", created_at)

    def find_pending(self, action_type: str, payload: dict[str, str]) -> ActionProposal | None:
        payload_json = json.dumps(payload, sort_keys=True)
        with sqlite3.connect(self._database_path) as connection:
            row = connection.execute(
                "SELECT id, action_type, payload_json, status, created_at, reviewed_at "
                "FROM action_proposals WHERE action_type = ? AND payload_json = ? AND status = 'pending'",
                (action_type, payload_json),
            ).fetchone()
        return self._from_row(row) if row is not None else None

    def get(self, proposal_id: int) -> ActionProposal | None:
        with sqlite3.connect(self._database_path) as connection:
            row = connection.execute(
                "SELECT id, action_type, payload_json, status, created_at, reviewed_at "
                "FROM action_proposals WHERE id = ?",
                (proposal_id,),
            ).fetchone()
        return self._from_row(row) if row is not None else None

    def list_all(self) -> list[ActionProposal]:
        with sqlite3.connect(self._database_path) as connection:
            rows = connection.execute(
                "SELECT id, action_type, payload_json, status, created_at, reviewed_at "
                "FROM action_proposals ORDER BY id"
            ).fetchall()
        return [self._from_row(row) for row in rows]

    def set_status(self, proposal_id: int, status: str) -> None:
        if status not in {"accepted", "rejected"}:
            raise ValueError("Action proposal status must be accepted or rejected.")
        with sqlite3.connect(self._database_path) as connection:
            cursor = connection.execute(
                "UPDATE action_proposals SET status = ?, reviewed_at = ? "
                "WHERE id = ? AND status = 'pending'",
                (status, datetime.now(UTC).isoformat(), proposal_id),
            )
        if cursor.rowcount != 1:
            raise ValueError("Action proposal was not found or was already reviewed.")

    @staticmethod
    def _from_row(row: tuple[object, ...]) -> ActionProposal:
        return ActionProposal(
            id=int(row[0]),
            action_type=str(row[1]),
            payload={str(key): str(value) for key, value in json.loads(str(row[2])).items()},
            status=str(row[3]),
            created_at=datetime.fromisoformat(str(row[4])),
            reviewed_at=datetime.fromisoformat(str(row[5])) if row[5] else None,
        )


class ActionProposalService:
    """Create proposals freely, but execute a state change only after review."""

    CREATE_WORKSPACE = "create_workspace"

    def __init__(
        self,
        repository: ActionProposalRepository,
        workspaces: WorkspaceRepository,
        activity: ActivityService,
    ) -> None:
        self._repository = repository
        self._workspaces = workspaces
        self._activity = activity

    def propose_workspace_creation(self, name: str) -> tuple[ActionProposal | None, Workspace | None]:
        normalized = " ".join(name.split())
        if not normalized:
            raise ValueError("A workspace name must not be empty.")
        existing = self._workspace_named(normalized)
        if existing is not None:
            return None, existing
        payload = {"name": normalized}
        proposal = self._repository.find_pending(self.CREATE_WORKSPACE, payload)
        if proposal is None:
            proposal = self._repository.add(self.CREATE_WORKSPACE, payload)
            self._activity.record(
                ActivityType.ACTION_PROPOSED,
                object_id=str(proposal.id),
                details=f"Create workspace: {normalized}",
            )
        return proposal, None

    def review(self, proposal_id: int, decision: str) -> tuple[ActionProposal, Workspace | None]:
        if decision not in {"accepted", "rejected"}:
            raise ValueError("Action proposal decision must be accepted or rejected.")
        proposal = self._repository.get(proposal_id)
        if proposal is None:
            raise ValueError("Action proposal was not found.")
        if proposal.status == decision:
            return proposal, self._workspace_named(proposal.payload["name"])
        if proposal.status != "pending":
            raise ValueError(f"Action proposal {proposal_id} was already {proposal.status}.")
        workspace = None
        if decision == "accepted":
            workspace = self._workspace_named(proposal.payload["name"])
            if workspace is None:
                workspace = WorkspaceService(self._workspaces, self._activity).create(
                    proposal.payload["name"]
                )
        self._repository.set_status(proposal_id, decision)
        self._activity.record(
            ActivityType.ACTION_ACCEPTED if decision == "accepted" else ActivityType.ACTION_REJECTED,
            object_id=str(proposal_id),
            details=proposal.action_type,
        )
        reviewed = self._repository.get(proposal_id)
        if reviewed is None:
            raise RuntimeError("Reviewed action proposal disappeared.")
        return reviewed, workspace

    def _workspace_named(self, name: str) -> Workspace | None:
        normalized = name.casefold()
        return next(
            (workspace for workspace in self._workspaces.list_all() if workspace.name.casefold() == normalized),
            None,
        )
