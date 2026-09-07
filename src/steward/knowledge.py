"""Canonical concepts and aliases, distinct from source files and summaries."""
from __future__ import annotations
import sqlite3
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path

@dataclass(frozen=True, slots=True)
class Concept:
    id: int | None
    name: str
    created_at: datetime

@dataclass(frozen=True, slots=True)
class ConceptProposal:
    existing_matches: tuple[int, ...]
    new_concepts: tuple[str, ...]
    supporting_fragment_ids: tuple[int, ...]

class KnowledgeService:
    def __init__(self, database_path: Path) -> None: self._database_path = database_path
    def create_concept(self, name: str) -> Concept:
        concept = Concept(None, name.strip(), datetime.now(UTC))
        if not concept.name: raise ValueError("Concept name must not be empty.")
        with sqlite3.connect(self._database_path) as connection:
            cursor=connection.execute("INSERT INTO concepts (name,created_at) VALUES (?,?)",(concept.name,concept.created_at.isoformat()))
        return replace(concept,id=int(cursor.lastrowid))
    def add_alias(self, concept_id: int, alias: str) -> None:
        if not alias.strip(): raise ValueError("Alias must not be empty.")
        with sqlite3.connect(self._database_path) as connection:
            connection.execute("INSERT OR IGNORE INTO concept_aliases (concept_id,alias) VALUES (?,?)",(concept_id,alias.strip()))
    def find(self, name_or_alias: str) -> Concept | None:
        with sqlite3.connect(self._database_path) as connection:
            row = connection.execute("SELECT id,name,created_at FROM concepts WHERE name = ? UNION SELECT c.id,c.name,c.created_at FROM concepts c JOIN concept_aliases a ON a.concept_id=c.id WHERE a.alias = ?", (name_or_alias.strip(), name_or_alias.strip())).fetchone()
        return Concept(int(row[0]), str(row[1]), datetime.fromisoformat(str(row[2]))) if row else None
    def propose(self, names: list[str], fragment_ids: list[int]) -> ConceptProposal:
        existing=[]; new=[]
        for name in names:
            concept=self.find(name)
            (existing if concept else new).append(concept.id if concept else name)
        return ConceptProposal(tuple(existing), tuple(new), tuple(fragment_ids))
