"""SQLite persistence for derived source fragments."""

from __future__ import annotations

import sqlite3
from dataclasses import replace
from pathlib import Path

from steward.extraction.models import ExtractionResult, SourceFragment


class UnknownSourceError(ValueError):
    """Raised when fragment persistence targets a Source absent from SQLite."""


class SourceFragmentRepository:
    """Replace and retrieve the derived fragments belonging to one Source."""

    def __init__(self, database_path: Path) -> None:
        self._database_path = database_path

    def replace_for_source(self, result: ExtractionResult) -> tuple[SourceFragment, ...]:
        """Atomically replace every derived fragment for one Source."""
        with sqlite3.connect(self._database_path) as connection:
            connection.execute("PRAGMA foreign_keys = ON")
            source_exists = connection.execute(
                "SELECT 1 FROM sources WHERE id = ?", (result.source_id,)
            ).fetchone()
            if source_exists is None:
                raise UnknownSourceError(f"Source id {result.source_id} is not registered.")

            connection.execute(
                "DELETE FROM source_fragments WHERE source_id = ?", (result.source_id,)
            )
            stored_fragments: list[SourceFragment] = []
            for fragment in result.fragments:
                cursor = connection.execute(
                    """
                    INSERT INTO source_fragments (
                        source_id, heading, ordinal, text, location
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        fragment.source_id,
                        fragment.heading,
                        fragment.ordinal,
                        fragment.text,
                        fragment.location,
                    ),
                )
                if cursor.lastrowid is None:
                    raise RuntimeError("SQLite did not assign an ID to the SourceFragment.")
                stored_fragments.append(replace(fragment, id=cursor.lastrowid))

        return tuple(stored_fragments)

    def list_for_source(self, source_id: int) -> tuple[SourceFragment, ...]:
        """Return a Source's fragments in their original document order."""
        with sqlite3.connect(self._database_path) as connection:
            rows = connection.execute(
                """
                SELECT id, source_id, heading, ordinal, text, location
                FROM source_fragments
                WHERE source_id = ?
                ORDER BY ordinal
                """,
                (source_id,),
            ).fetchall()

        return tuple(
            SourceFragment(
                id=int(row[0]),
                source_id=int(row[1]),
                heading=str(row[2]) if row[2] is not None else None,
                ordinal=int(row[3]),
                text=str(row[4]),
                location=str(row[5]),
            )
            for row in rows
        )
