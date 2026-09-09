"""Explicit local filesystem roots authorized for Steward scanning."""
from __future__ import annotations

import sqlite3
import json
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
    exclusions: tuple[Path, ...] = ()

    @property
    def health(self) -> str:
        """Return a conservative operational state without changing authorization."""
        if not self.enabled:
            return "disabled"
        return "available" if self.path.is_dir() else "missing"


class SourceRootRepository:
    def __init__(self, database_path: Path) -> None:
        self._database_path = database_path

    def add(self, name: str, path: Path, *, exclusions: tuple[Path, ...] = ()) -> SourceRoot:
        resolved = path.resolve()
        if not name.strip():
            raise ValueError("Source root name must not be empty.")
        if not resolved.is_dir():
            raise ValueError(f"Source root must be an existing directory: {resolved}")
        normalized_exclusions = self._normalize_exclusions(resolved, exclusions)
        created = datetime.now(UTC)
        try:
            with sqlite3.connect(self._database_path) as connection:
                cursor = connection.execute(
                    "INSERT INTO source_roots (name, path, enabled, created_at, exclusions) VALUES (?, ?, 1, ?, ?)",
                    (name.strip(), str(resolved), created.isoformat(), json.dumps([str(item) for item in normalized_exclusions])),
                )
        except sqlite3.IntegrityError as error:
            raise ValueError("A source root already uses that name or path.") from error
        return SourceRoot(int(cursor.lastrowid), name.strip(), resolved, True, created, normalized_exclusions)

    def list_all(self) -> tuple[SourceRoot, ...]:
        with sqlite3.connect(self._database_path) as connection:
            rows = connection.execute(
                "SELECT id, name, path, enabled, created_at, exclusions FROM source_roots ORDER BY name"
            ).fetchall()
        return tuple(self._from_row(row) for row in rows)

    def get_by_name(self, name: str) -> SourceRoot | None:
        with sqlite3.connect(self._database_path) as connection:
            row = connection.execute(
                "SELECT id, name, path, enabled, created_at, exclusions FROM source_roots WHERE name = ?",
                (name.strip(),),
            ).fetchone()
        return self._from_row(row) if row is not None else None

    def set_enabled(self, name: str, enabled: bool) -> SourceRoot:
        root = self.get_by_name(name)
        if root is None:
            raise ValueError(f"No locally authorized source root named {name!r}.")
        if root.enabled == enabled:
            return root
        with sqlite3.connect(self._database_path) as connection:
            connection.execute("UPDATE source_roots SET enabled = ? WHERE id = ?", (int(enabled), root.id))
        return SourceRoot(root.id, root.name, root.path, enabled, root.created_at, root.exclusions)

    @staticmethod
    def _normalize_exclusions(root: Path, exclusions: tuple[Path, ...]) -> tuple[Path, ...]:
        normalized: list[Path] = []
        for exclusion in exclusions:
            candidate = (exclusion if exclusion.is_absolute() else root / exclusion).resolve()
            if candidate == root or not candidate.is_relative_to(root):
                raise ValueError("A source-root exclusion must be a directory beneath its root.")
            relative = candidate.relative_to(root)
            if relative not in normalized:
                normalized.append(relative)
        return tuple(sorted(normalized, key=lambda item: item.as_posix().casefold()))

    @staticmethod
    def _from_row(row: tuple[object, ...]) -> SourceRoot:
        root = Path(str(row[2]))
        exclusions = tuple(Path(item) for item in json.loads(str(row[5])))
        return SourceRoot(
            int(row[0]), str(row[1]), root, bool(row[3]), datetime.fromisoformat(str(row[4])), exclusions
        )
