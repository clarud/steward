"""Cached summaries, keyed by file version and model, so asking again is instant."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path


@dataclass(frozen=True, slots=True)
class StoredSummary:
    text: str
    cited_keys: tuple[str, ...]
    covered: int
    total: int
    skipped: tuple[str, ...]


class SummaryRepository:
    def __init__(self, database_path: Path) -> None:
        self._database_path = database_path

    def get(self, source_id: int, content_hash: str, model: str) -> StoredSummary | None:
        with sqlite3.connect(self._database_path) as connection:
            row = connection.execute(
                "SELECT text, cited_json, covered, total, skipped_json FROM source_summaries "
                "WHERE source_id = ? AND content_hash = ? AND model = ?",
                (source_id, content_hash, model),
            ).fetchone()
        if row is None:
            return None
        return StoredSummary(str(row[0]), tuple(json.loads(row[1])), int(row[2]), int(row[3]), tuple(json.loads(row[4])))

    def put(self, source_id: int, content_hash: str, model: str, summary: StoredSummary) -> None:
        with sqlite3.connect(self._database_path) as connection:
            connection.execute("PRAGMA foreign_keys = ON")
            # An edited file gets a new hash; older versions' summaries are no longer useful.
            connection.execute(
                "DELETE FROM source_summaries WHERE source_id = ? AND content_hash != ?", (source_id, content_hash)
            )
            connection.execute(
                """INSERT OR REPLACE INTO source_summaries
                   (source_id, content_hash, model, text, cited_json, covered, total, skipped_json, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (source_id, content_hash, model, summary.text, json.dumps(summary.cited_keys), summary.covered,
                 summary.total, json.dumps(summary.skipped), datetime.now(UTC).isoformat()),
            )
