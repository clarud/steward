"""Explicit local filesystem roots authorized for Steward scanning."""
from __future__ import annotations

import sqlite3
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from steward.sources.hashing import hash_file

if TYPE_CHECKING:
    from steward.sources.scanning import ScanResult


@dataclass(frozen=True, slots=True)
class SourceRoot:
    id: int | None
    name: str
    path: Path
    enabled: bool
    created_at: datetime
    exclusions: tuple[Path, ...] = ()
    last_scanned_at: datetime | None = None
    last_scan_counts: tuple[int, int, int, int] | None = None

    @property
    def health(self) -> str:
        """Return a conservative operational state without changing authorization."""
        if not self.enabled:
            return "disabled"
        return "available" if self.path.is_dir() else "missing"


@dataclass(frozen=True, slots=True)
class RootRelocation:
    root: SourceRoot
    updated_sources: int


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
                """SELECT roots.id, roots.name, roots.path, roots.enabled, roots.created_at, roots.exclusions,
                          scans.scanned_at, scans.new_count, scans.updated_count,
                          scans.unchanged_count, scans.missing_count
                   FROM source_roots AS roots
                   LEFT JOIN source_root_scans AS scans ON scans.id = (
                       SELECT id FROM source_root_scans
                       WHERE root_id = roots.id ORDER BY scanned_at DESC, id DESC LIMIT 1
                   )
                   ORDER BY roots.name"""
            ).fetchall()
        return tuple(self._from_row(row) for row in rows)

    def get_by_name(self, name: str) -> SourceRoot | None:
        with sqlite3.connect(self._database_path) as connection:
            row = connection.execute(
                """SELECT roots.id, roots.name, roots.path, roots.enabled, roots.created_at, roots.exclusions,
                          scans.scanned_at, scans.new_count, scans.updated_count,
                          scans.unchanged_count, scans.missing_count
                   FROM source_roots AS roots
                   LEFT JOIN source_root_scans AS scans ON scans.id = (
                       SELECT id FROM source_root_scans
                       WHERE root_id = roots.id ORDER BY scanned_at DESC, id DESC LIMIT 1
                   )
                   WHERE roots.name = ?""",
                (name.strip(),),
            ).fetchone()
        return self._from_row(row) if row is not None else None

    def record_successful_scan(self, root: SourceRoot, result: ScanResult) -> SourceRoot:
        """Persist concise local reconciliation telemetry after a completed scan."""

        if root.id is None:
            raise ValueError("A persisted source root ID is required to record a scan.")
        scanned_at = datetime.now(UTC)
        with sqlite3.connect(self._database_path) as connection:
            connection.execute(
                """INSERT INTO source_root_scans
                    (root_id, scanned_at, new_count, updated_count, unchanged_count, missing_count)
                    VALUES (?, ?, ?, ?, ?, ?)""",
                (root.id, scanned_at.isoformat(), result.new, result.updated, result.unchanged, result.missing),
            )
        return SourceRoot(
            root.id, root.name, root.path, root.enabled, root.created_at,
            root.exclusions, scanned_at, (result.new, result.updated, result.unchanged, result.missing),
        )

    def set_enabled(self, name: str, enabled: bool) -> SourceRoot:
        root = self.get_by_name(name)
        if root is None:
            raise ValueError(f"No locally authorized source root named {name!r}.")
        if root.enabled == enabled:
            return root
        with sqlite3.connect(self._database_path) as connection:
            connection.execute("UPDATE source_roots SET enabled = ? WHERE id = ?", (int(enabled), root.id))
        return SourceRoot(
            root.id, root.name, root.path, enabled, root.created_at, root.exclusions,
            root.last_scanned_at, root.last_scan_counts,
        )

    def relocate_missing(self, name: str, new_path: Path) -> RootRelocation:
        """Rebind a missing root only when all tracked originals match by hash."""
        destination = new_path.resolve()
        if not destination.is_dir():
            raise ValueError("The replacement source root must be an existing directory.")
        with sqlite3.connect(self._database_path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT id, name, path, enabled, created_at, exclusions FROM source_roots WHERE name = ?",
                (name.strip(),),
            ).fetchone()
            if row is None:
                raise ValueError(f"No locally authorized source root named {name!r}.")
            root = self._from_row(row)
            old_path = root.path.resolve()
            if old_path.is_dir():
                raise ValueError("The existing source root is still available; disable competing copies before relocation.")
            conflict = connection.execute(
                "SELECT 1 FROM source_roots WHERE path = ? AND id <> ?", (str(destination), root.id)
            ).fetchone()
            if conflict is not None:
                raise ValueError("The replacement directory is already authorized as another source root.")
            rows = connection.execute(
                "SELECT id, path, content_hash FROM sources ORDER BY id"
            ).fetchall()
            replacements: list[tuple[str, int, int, str, str]] = []
            for source_id, raw_path, expected_hash in rows:
                source_path = Path(str(raw_path)).resolve()
                if not source_path.is_relative_to(old_path):
                    continue
                candidate = (destination / source_path.relative_to(old_path)).resolve()
                if not candidate.is_relative_to(destination) or not candidate.is_file():
                    raise ValueError(f"Replacement root is missing tracked source {source_id} at its relative location.")
                if hash_file(candidate) != str(expected_hash):
                    raise ValueError(f"Replacement root source {source_id} does not match its registered content hash.")
                path_conflict = connection.execute(
                    "SELECT 1 FROM sources WHERE path = ? AND id <> ?", (str(candidate), source_id)
                ).fetchone()
                if path_conflict is not None:
                    raise ValueError(f"Replacement path for source {source_id} is already registered to another source.")
                stat = candidate.stat()
                replacements.append((str(candidate), int(source_id), stat.st_size,
                                     datetime.fromtimestamp(stat.st_mtime, UTC).isoformat(),
                                     datetime.now(UTC).isoformat()))
            for candidate, source_id, size_bytes, modified_at, last_seen_at in replacements:
                connection.execute(
                    "UPDATE sources SET path = ?, size_bytes = ?, modified_at = ?, last_seen_at = ?, status = 'active' WHERE id = ?",
                    (candidate, size_bytes, modified_at, last_seen_at, source_id),
                )
            connection.execute("UPDATE source_roots SET path = ? WHERE id = ?", (str(destination), root.id))
        relocated = SourceRoot(
            root.id, root.name, destination, root.enabled, root.created_at, root.exclusions,
            root.last_scanned_at, root.last_scan_counts,
        )
        return RootRelocation(relocated, len(replacements))

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
            int(row[0]), str(row[1]), root, bool(row[3]), datetime.fromisoformat(str(row[4])), exclusions,
            datetime.fromisoformat(str(row[6])) if len(row) > 6 and row[6] is not None else None,
            (int(row[7]), int(row[8]), int(row[9]), int(row[10]))
            if len(row) > 10 and row[7] is not None else None,
        )
