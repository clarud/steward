"""SQLite persistence for derived source fragments."""

from __future__ import annotations

import sqlite3
from collections.abc import Collection
from dataclasses import dataclass, replace
from pathlib import Path

from steward.extraction.models import ExtractionResult, SourceFragment
from steward.sources.models import SourceStatus, SourceType


def storable_text(value: str | None) -> str | None:
    """Return text SQLite can store as UTF-8.

    Some PDF text layers yield math symbols (for example U+1D465) as UTF-16
    surrogate pairs. Rejoin valid pairs into the real character and replace
    any unpaired half with U+FFFD, rather than failing the whole write.
    """
    if value is None:
        return None
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        return value.encode("utf-16", "surrogatepass").decode("utf-16", "replace")
    return value


class UnknownSourceError(ValueError):
    """Raised when fragment persistence targets a Source absent from SQLite."""


class InvalidSearchQueryError(ValueError):
    """Raised when a query is not valid SQLite FTS5 syntax."""


@dataclass(frozen=True, slots=True)
class FragmentSearchResult:
    """One lexical match with SQLite FTS5's BM25 ranking score."""

    fragment: SourceFragment
    score: float
    highlighted_text: str | None = None


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
            connection.execute(
                "DELETE FROM source_fragments_fts WHERE source_id = ?", (result.source_id,)
            )
            stored_fragments: list[SourceFragment] = []
            for fragment in result.fragments:
                fragment = replace(
                    fragment,
                    heading=storable_text(fragment.heading),
                    text=storable_text(fragment.text) or "",
                    location=storable_text(fragment.location) or "",
                )
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
                stored_fragment = replace(fragment, id=cursor.lastrowid)
                connection.execute(
                    """
                    INSERT INTO source_fragments_fts (
                        fragment_id, source_id, heading, text
                    ) VALUES (?, ?, ?, ?)
                    """,
                    (
                        stored_fragment.id,
                        stored_fragment.source_id,
                        stored_fragment.heading,
                        stored_fragment.text,
                    ),
                )
                stored_fragments.append(stored_fragment)

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

    def get(self, fragment_id: int) -> SourceFragment | None:
        """Return one fragment by stable ID for an evidence-bounded workflow."""
        with sqlite3.connect(self._database_path) as connection:
            row = connection.execute(
                "SELECT id, source_id, heading, ordinal, text, location FROM source_fragments WHERE id = ?",
                (fragment_id,),
            ).fetchone()
        if row is None:
            return None
        return SourceFragment(
            id=int(row[0]), source_id=int(row[1]), heading=str(row[2]) if row[2] is not None else None,
            ordinal=int(row[3]), text=str(row[4]), location=str(row[5]),
        )

    def search(
        self,
        query: str,
        *,
        limit: int = 5,
        source_types: Collection[SourceType] | None = None,
        path_prefix: Path | None = None,
    ) -> tuple[FragmentSearchResult, ...]:
        """Return fragments ranked by FTS5 BM25 lexical relevance."""
        if not query.strip():
            raise ValueError("Search query must not be empty.")
        if limit <= 0:
            raise ValueError("Search limit must be positive.")

        selected_source_types = tuple(sorted({source_type.value for source_type in source_types or ()}))
        source_type_filter = (
            f" AND s.source_type IN ({', '.join('?' for _ in selected_source_types)})"
            if selected_source_types
            else ""
        )
        normalized_prefix = str(path_prefix.resolve()) if path_prefix is not None else None
        path_filter = " AND s.path LIKE ?" if normalized_prefix is not None else ""
        def fetch(match_query: str) -> list[tuple[object, ...]]:
            parameters = (
                match_query, SourceStatus.ACTIVE.value, *selected_source_types,
                *((normalized_prefix + "%",) if normalized_prefix is not None else ()), limit,
            )
            with sqlite3.connect(self._database_path) as connection:
                return connection.execute(
                    f"""
                    SELECT sf.id, sf.source_id, sf.heading, sf.ordinal, sf.text,
                           sf.location, bm25(source_fragments_fts) AS score,
                           highlight(source_fragments_fts, 3, '[', ']') AS highlighted_text
                    FROM source_fragments_fts
                    JOIN source_fragments AS sf
                      ON sf.id = source_fragments_fts.fragment_id
                    JOIN sources AS s ON s.id = sf.source_id
                    WHERE source_fragments_fts MATCH ?
                      AND s.status = ?
                      {source_type_filter}
                      {path_filter}
                    ORDER BY score
                    LIMIT ?
                    """,
                    parameters,
                ).fetchall()

        try:
            rows = fetch(query)
        except sqlite3.OperationalError:
            # Preserve valid FTS operators, but let ordinary filename-like or
            # punctuation-heavy requests fall back to one literal FTS phrase.
            # Escaping quotes keeps user input data, never FTS syntax.
            literal_query = '"' + query.replace('"', '""') + '"'
            try:
                rows = fetch(literal_query)
            except sqlite3.OperationalError as error:
                raise InvalidSearchQueryError(f"Invalid FTS5 search query: {query!r}") from error

        return tuple(
            FragmentSearchResult(
                fragment=SourceFragment(
                    id=int(row[0]),
                    source_id=int(row[1]),
                    heading=str(row[2]) if row[2] is not None else None,
                    ordinal=int(row[3]),
                    text=str(row[4]),
                    location=str(row[5]),
                ),
                score=float(row[6]),
                highlighted_text=str(row[7]) if row[7] is not None else None,
            )
            for row in rows
        )
