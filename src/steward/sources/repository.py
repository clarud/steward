"""SQLite persistence for Source metadata."""

from __future__ import annotations

import sqlite3
from collections.abc import Collection
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

    def search_filenames(
        self,
        query: str,
        *,
        limit: int = 5,
        source_types: Collection[SourceType] | None = None,
        path_prefix: Path | None = None,
    ) -> tuple[Source, ...]:
        """Find active originals by filename/path terms, without reading content.

        This is a deliberately conservative fallback for cases where someone
        remembers a filename, folder term, or extension but the phrase is not
        in an extracted fragment. It is not an arbitrary filesystem search.
        """
        terms = tuple(token.casefold() for token in query.split() if token.strip())
        if not terms:
            return ()
        if limit <= 0:
            raise ValueError("Search limit must be positive.")
        selected_types = tuple(sorted({item.value for item in source_types or ()}))
        type_filter = (
            f" AND source_type IN ({', '.join('?' for _ in selected_types)})"
            if selected_types else ""
        )
        normalized_prefix = str(path_prefix.resolve()) if path_prefix is not None else None
        path_filter = " AND path LIKE ?" if normalized_prefix is not None else ""
        term_filter = "".join(" AND lower(path) LIKE ?" for _ in terms)
        with sqlite3.connect(self._database_path) as connection:
            rows = connection.execute(
                f"""SELECT id, path, content_hash, source_type, size_bytes, modified_at,
                           first_seen_at, last_seen_at, status
                    FROM sources
                    WHERE status = ?{type_filter}{path_filter}{term_filter}
                    ORDER BY path COLLATE NOCASE, id
                    LIMIT ?""",
                (
                    SourceStatus.ACTIVE.value,
                    *selected_types,
                    *((normalized_prefix + "%",) if normalized_prefix is not None else ()),
                    *(f"%{term}%" for term in terms),
                    limit,
                ),
            ).fetchall()
        return tuple(self._source_from_row(row) for row in rows)

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

    def unregister(self, source_id: int) -> Source:
        """Remove one Source's local registry and derived data, never its file.

        This is intentionally not named ``delete``: a Source represents original
        evidence on the filesystem, while this repository owns only SQLite
        operational metadata.  Foreign-key cascades remove relationships and
        derived rows; the separate FTS table is cleared explicitly.
        """
        source = self.get_by_id(source_id)
        if source is None:
            raise SourceNotFoundError(f"Source id {source_id} is not registered.")

        with sqlite3.connect(self._database_path) as connection:
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute(
                "DELETE FROM source_fragments_fts WHERE source_id = ?", (source_id,)
            )
            cursor = connection.execute("DELETE FROM sources WHERE id = ?", (source_id,))

        if cursor.rowcount != 1:
            raise SourceNotFoundError(f"Source id {source_id} is not registered.")
        return source

    def replace_discovered_move(self, missing_source_id: int, discovered_source_id: int) -> Source:
        """Preserve a missing source ID by replacing its temporary moved-path row.

        Callers must have already established an unambiguous, user-approved
        content-hash match. The original filesystem is never touched.
        """

        missing = self.get_by_id(missing_source_id)
        discovered = self.get_by_id(discovered_source_id)
        if missing is None or discovered is None:
            raise SourceNotFoundError("Both proposed sources must still be registered.")
        if missing.status is not SourceStatus.MISSING or discovered.status is not SourceStatus.ACTIVE:
            raise ValueError("The move proposal is no longer valid for the current source states.")
        if missing.content_hash != discovered.content_hash:
            raise ValueError("The proposed sources no longer have matching content hashes.")
        preserved = replace(
            missing, path=discovered.path, content_hash=discovered.content_hash,
            source_type=discovered.source_type, size_bytes=discovered.size_bytes,
            modified_at=discovered.modified_at, last_seen_at=discovered.last_seen_at,
            status=SourceStatus.ACTIVE,
        )
        with sqlite3.connect(self._database_path) as connection:
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute(
                "INSERT INTO source_location_history (source_id, path, recorded_at, reason) VALUES (?, ?, ?, 'move_accepted')",
                (missing_source_id, str(missing.path), datetime.now(missing.last_seen_at.tzinfo).isoformat()),
            )
            connection.execute("DELETE FROM source_fragments_fts WHERE source_id = ?", (discovered_source_id,))
            connection.execute("DELETE FROM sources WHERE id = ?", (discovered_source_id,))
            cursor = connection.execute(
                """UPDATE sources SET path = ?, content_hash = ?, source_type = ?, size_bytes = ?,
                       modified_at = ?, first_seen_at = ?, last_seen_at = ?, status = ? WHERE id = ?""",
                (str(preserved.path), preserved.content_hash, preserved.source_type.value,
                 preserved.size_bytes, preserved.modified_at.isoformat(),
                 preserved.first_seen_at.isoformat(), preserved.last_seen_at.isoformat(),
                 preserved.status.value, preserved.id),
            )
        if cursor.rowcount != 1:
            raise SourceNotFoundError(f"Source id {missing_source_id} is not registered.")
        return preserved

    def location_history(self, source_id: int) -> tuple[Path, ...]:
        with sqlite3.connect(self._database_path) as connection:
            rows = connection.execute(
                "SELECT path FROM source_location_history WHERE source_id = ? ORDER BY id", (source_id,)
            ).fetchall()
        return tuple(Path(str(row[0])) for row in rows)

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
