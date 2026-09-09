"""Explicit local filesystem roots authorized for Steward scanning."""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path


@dataclass(frozen=True, slots=True)
class SourceRoot:
    id: int | None
    name: str
    path: Path
    enabled: bool
    created_at: datetime


class SourceRootRepository:
    def __init__(self, database_path: Path) -> None:
        self._database_path = database_path

    def add(self, name: str, path: Path) -> SourceRoot:
        resolved = path.resolve()
        if not name.strip():
            raise ValueError("Source root name must not be empty.")
        if not resolved.is_dir():
            raise ValueError(f"Source root must be an existing directory: {resolved}")
        created = datetime.now(UTC)
        try:
            with sqlite3.connect(self._database_path) as connection:
                cursor = connection.execute(
                    "INSERT INTO source_roots (name, path, enabled, created_at) VALUES (?, ?, 1, ?)",
                    (name.strip(), str(resolved), created.isoformat()),
                )
        except sqlite3.IntegrityError as error:
            raise ValueError("A source root already uses that name or path.") from error
        return SourceRoot(int(cursor.lastrowid), name.strip(), resolved, True, created)

    def list_all(self) -> tuple[SourceRoot, ...]:
        with sqlite3.connect(self._database_path) as connection:
            rows = connection.execute(
                "SELECT id, name, path, enabled, created_at FROM source_roots ORDER BY name"
            ).fetchall()
        return tuple(SourceRoot(int(r[0]), str(r[1]), Path(str(r[2])), bool(r[3]), datetime.fromisoformat(str(r[4]))) for r in rows)
