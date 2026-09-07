"""Explicit workspace context and its source relationships."""
from __future__ import annotations
import sqlite3
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path

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

class WorkspaceService:
    def __init__(self, repository: WorkspaceRepository) -> None: self._repository = repository
    def create(self, name: str) -> Workspace: return self._repository.create(name)
    def add_source(self, workspace_id: int, source_id: int) -> None: self._repository.link_source(workspace_id, source_id)
