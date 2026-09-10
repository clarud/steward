"""Explicit workspace context and its source relationships."""
from __future__ import annotations
import sqlite3
import json
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from steward.activity import ActivityService, ActivityType

@dataclass(frozen=True, slots=True)
class Workspace:
    id: int | None
    name: str
    status: str
    created_at: datetime

    def __post_init__(self) -> None:
        if self.id is not None and self.id <= 0: raise ValueError("Workspace id must be positive.")
        if not self.name.strip(): raise ValueError("Workspace name must not be empty.")
        if self.created_at.tzinfo is None: raise ValueError("Workspace time must include a timezone.")

class WorkspaceRepository:
    def __init__(self, database_path: Path) -> None: self._database_path = database_path
    def create(self, name: str) -> Workspace:
        workspace = Workspace(None, name.strip(), "active", datetime.now(UTC))
        if not workspace.name: raise ValueError("Workspace name must not be empty.")
        with sqlite3.connect(self._database_path) as connection:
            cursor = connection.execute("INSERT INTO workspaces (name,status,created_at) VALUES (?,?,?)", (workspace.name, workspace.status, workspace.created_at.isoformat()))
        return replace(workspace, id=cursor.lastrowid)
    def link_source(self, workspace_id: int, source_id: int) -> None:
        with sqlite3.connect(self._database_path) as connection:
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("INSERT OR IGNORE INTO workspace_sources (workspace_id,source_id) VALUES (?,?)", (workspace_id,source_id))

    def list_all(self) -> list[Workspace]:
        with sqlite3.connect(self._database_path) as connection:
            rows = connection.execute("SELECT id,name,status,created_at FROM workspaces ORDER BY name").fetchall()
        return [Workspace(int(row[0]), str(row[1]), str(row[2]), datetime.fromisoformat(str(row[3]))) for row in rows]

    def review_link_proposal(self, proposal_id: int, decision: str) -> tuple[int, int]:
        """Atomically review a semantic link, including its durable audit events."""
        if decision not in {"accepted", "rejected"}:
            raise ValueError("Invalid link review decision.")
        with sqlite3.connect(self._database_path) as connection:
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT action_type, payload_json, status FROM action_proposals WHERE id = ?",
                (proposal_id,),
            ).fetchone()
            if row is None or row[0] != "link_source_to_workspace":
                raise ValueError("Link proposal was not found.")
            payload = json.loads(row[1])
            workspace_id, source_id = int(payload["workspace_id"]), int(payload["source_id"])
            if row[2] == decision:
                return workspace_id, source_id
            if row[2] != "pending":
                raise ValueError(f"Link proposal {proposal_id} was already {row[2]}.")
            now = datetime.now(UTC).isoformat()
            if decision == "accepted":
                workspace = connection.execute("SELECT status FROM workspaces WHERE id = ?", (workspace_id,)).fetchone()
                source = connection.execute("SELECT status FROM sources WHERE id = ?", (source_id,)).fetchone()
                if workspace != ("active",) or source != ("active",):
                    raise ValueError("The workspace or source is no longer available; the link was not created.")
                added = connection.execute(
                    "INSERT OR IGNORE INTO workspace_sources (workspace_id, source_id) VALUES (?, ?)",
                    (workspace_id, source_id),
                ).rowcount
                if added:
                    connection.execute(
                        "INSERT INTO activity_events (event_type, object_id, details, occurred_at) VALUES (?, ?, ?, ?)",
                        (ActivityType.SOURCE_LINKED_TO_WORKSPACE.value, str(source_id), f"workspace:{workspace_id}", now),
                    )
            connection.execute("UPDATE action_proposals SET status = ?, reviewed_at = ? WHERE id = ?",
                               (decision, now, proposal_id))
            connection.execute(
                "INSERT INTO activity_events (event_type, object_id, details, occurred_at) VALUES (?, ?, ?, ?)",
                (ActivityType.ACTION_ACCEPTED.value if decision == "accepted" else ActivityType.ACTION_REJECTED.value,
                 str(proposal_id), "link_source_to_workspace", now),
            )
        return workspace_id, source_id
    def list_source_ids(self, workspace_id: int) -> tuple[int, ...]:
        """Return semantic links without implying ownership or file movement."""
        with sqlite3.connect(self._database_path) as connection:
            rows = connection.execute(
                "SELECT source_id FROM workspace_sources WHERE workspace_id = ? ORDER BY source_id",
                (workspace_id,),
            ).fetchall()
        return tuple(int(row[0]) for row in rows)

class WorkspaceService:
    def __init__(self, repository: WorkspaceRepository, activity_service: ActivityService | None = None) -> None: self._repository = repository; self._activity_service = activity_service
    def create(self, name: str) -> Workspace:
        workspace = self._repository.create(name)
        if self._activity_service is not None: self._activity_service.record(ActivityType.WORKSPACE_CREATED, object_id=str(workspace.id), details=workspace.name)
        return workspace
    def add_source(self, workspace_id: int, source_id: int) -> None: self._repository.link_source(workspace_id, source_id)
