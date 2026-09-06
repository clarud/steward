"""SQLite persistence for Source metadata."""

from __future__ import annotations

import sqlite3
from dataclasses import replace
from datetime import datetime
from pathlib import Path

from steward.sources.models import Source, SourceStatus, SourceType


class SourceAlreadyExistsError(ValueError):
    """Raised when attempting to register an already-known physical path."""


class SourceNotFoundError(ValueError):
    """Raised when attempting to update a Source that is not registered."""


class SourceRepository:
    """Store and retrieve Source records from an initialized Steward database."""

    def __init__(self, database_path: Path) -> None:
        self._database_path = database_path

    def add(self, source: Source) -> Source:
        """Persist an unregistered Source and return it with its SQLite ID."""
        if source.id is not None:
            raise ValueError("Only an unregistered Source can be added.")

        try:
            with sqlite3.connect(self._database_path) as connection:
                cursor = connection.execute(
                    """
                    INSERT INTO sources (
                        path, content_hash, source_type, size_bytes, modified_at,
                        first_seen_at, last_seen_at, status
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(source.path),
                        source.content_hash,
                        source.source_type.value,
                        source.size_bytes,
                        source.modified_at.isoformat(),
                        source.first_seen_at.isoformat(),
                        source.last_seen_at.isoformat(),
                        source.status.value,
                    ),
                )
        except sqlite3.IntegrityError as error:
            raise SourceAlreadyExistsError(
                f"A Source is already registered for {source.path}."
            ) from error

        if cursor.lastrowid is None:
            raise RuntimeError("SQLite did not assign an ID to the new Source.")
        return replace(source, id=cursor.lastrowid)

    def get_by_path(self, path: Path) -> Source | None:
        """Return the Source at its current path, if it is registered."""
        with sqlite3.connect(self._database_path) as connection:
            row = connection.execute(
                """
                SELECT id, path, content_hash, source_type, size_bytes, modified_at,
                       first_seen_at, last_seen_at, status
                FROM sources
                WHERE path = ?
                """,
                (str(path),),
            ).fetchone()

        return self._source_from_row(row) if row is not None else None

    def get_by_id(self, source_id: int) -> Source | None:
        """Return one registered Source by its SQLite ID, if present."""
        with sqlite3.connect(self._database_path) as connection:
            row = connection.execute(
                """
                SELECT id, path, content_hash, source_type, size_bytes, modified_at,
                       first_seen_at, last_seen_at, status
                FROM sources
                WHERE id = ?
                """,
                (source_id,),
            ).fetchone()

        return self._source_from_row(row) if row is not None else None

    def update(self, source: Source) -> None:
        """Replace metadata for an already-persisted Source."""
        if source.id is None:
            raise ValueError("Only a persisted Source can be updated.")

        with sqlite3.connect(self._database_path) as connection:
            cursor = connection.execute(
                """
                UPDATE sources
                SET path = ?, content_hash = ?, source_type = ?, size_bytes = ?,
                    modified_at = ?, first_seen_at = ?, last_seen_at = ?, status = ?
                WHERE id = ?
                """,
                (
                    str(source.path),
                    source.content_hash,
                    source.source_type.value,
                    source.size_bytes,
                    source.modified_at.isoformat(),
                    source.first_seen_at.isoformat(),
                    source.last_seen_at.isoformat(),
                    source.status.value,
                    source.id,
                ),
            )

        if cursor.rowcount != 1:
            raise SourceNotFoundError(f"Source id {source.id} is not registered.")

    def list_active(self) -> list[Source]:
        """Return every Source whose current path was last observed as present."""
        with sqlite3.connect(self._database_path) as connection:
            rows = connection.execute(
                """
                SELECT id, path, content_hash, source_type, size_bytes, modified_at,
                       first_seen_at, last_seen_at, status
                FROM sources
                WHERE status = ?
                ORDER BY id
                """,
                (SourceStatus.ACTIVE.value,),
            ).fetchall()

        return [self._source_from_row(row) for row in rows]

    def list_all(self) -> list[Source]:
        """Return every registered Source, including missing files."""
        with sqlite3.connect(self._database_path) as connection:
            rows = connection.execute(
                """
                SELECT id, path, content_hash, source_type, size_bytes, modified_at,
                       first_seen_at, last_seen_at, status
                FROM sources
                ORDER BY id
                """
            ).fetchall()

        return [self._source_from_row(row) for row in rows]

    @staticmethod
    def _source_from_row(row: tuple[object, ...]) -> Source:
        """Translate SQLite's primitive values back into the Source domain model."""
        return Source(
            id=int(row[0]),
            path=Path(str(row[1])),
            content_hash=str(row[2]),
            source_type=SourceType(str(row[3])),
            size_bytes=int(row[4]),
            modified_at=datetime.fromisoformat(str(row[5])),
            first_seen_at=datetime.fromisoformat(str(row[6])),
            last_seen_at=datetime.fromisoformat(str(row[7])),
            status=SourceStatus(str(row[8])),
        )
