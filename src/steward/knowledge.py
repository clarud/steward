"""Canonical concepts and aliases, distinct from source files and summaries."""
from __future__ import annotations
import sqlite3
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from enum import StrEnum
import re

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

@dataclass(frozen=True, slots=True)
class Claim:
    id: int | None
    concept_id: int
    text: str
    created_at: datetime

class EnrichmentOperation(StrEnum):
    CONFIRM = "confirm"
    EXTEND = "extend"
    REFINE = "refine"
    QUALIFY = "qualify"
    CONTRADICT = "contradict"

@dataclass(frozen=True, slots=True)
class KnowledgeEnrichmentProposal:
    claim_id: int
    fragment_id: int
    operation: EnrichmentOperation
    rationale: str

class KnowledgeService:
    def __init__(self, database_path: Path) -> None: self._database_path = database_path
    def create_concept(self, name: str) -> Concept:
        concept = Concept(None, name.strip(), datetime.now(UTC))
        if not concept.name: raise ValueError("Concept name must not be empty.")
        with sqlite3.connect(self._database_path) as connection:
            connection.execute("PRAGMA foreign_keys = ON")
            cursor=connection.execute("INSERT INTO concepts (name,created_at) VALUES (?,?)",(concept.name,concept.created_at.isoformat()))
        return replace(concept,id=int(cursor.lastrowid))
    def add_alias(self, concept_id: int, alias: str) -> None:
        if not alias.strip(): raise ValueError("Alias must not be empty.")
        with sqlite3.connect(self._database_path) as connection:
            connection.execute("PRAGMA foreign_keys = ON")
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
    def create_claim(self, concept_id: int, text: str, fragment_ids: list[int]) -> Claim:
        claim=Claim(None, concept_id, text.strip(), datetime.now(UTC))
        if not claim.text or not fragment_ids: raise ValueError("Claims require text and evidence.")
        with sqlite3.connect(self._database_path) as connection:
            connection.execute("PRAGMA foreign_keys = ON")
            cursor=connection.execute("INSERT INTO claims (concept_id,text,created_at) VALUES (?,?,?)",(concept_id,claim.text,claim.created_at.isoformat()))
            claim_id=int(cursor.lastrowid)
            connection.executemany("INSERT INTO claim_evidence (claim_id,fragment_id) VALUES (?,?)",[(claim_id,fragment_id) for fragment_id in fragment_ids])
        return replace(claim,id=claim_id)
    def compare_evidence(self, claim: Claim, *, fragment_id: int, evidence_text: str) -> KnowledgeEnrichmentProposal:
        claim_words=set(re.findall(r"\w+", claim.text.casefold())); evidence_words=set(re.findall(r"\w+", evidence_text.casefold()))
        if {"not", "never", "false", "incorrect"} & evidence_words:
            operation=EnrichmentOperation.CONTRADICT; rationale="The evidence contains an explicit negation."
        elif {"may", "might", "sometimes", "usually", "typically", "depends"} & evidence_words:
            operation=EnrichmentOperation.QUALIFY; rationale="The evidence limits the scope or certainty of the claim."
        elif {"instead", "revised", "updated", "replace"} & evidence_words:
            operation=EnrichmentOperation.REFINE; rationale="The evidence indicates a more specific or revised formulation."
        elif claim_words <= evidence_words:
            operation=EnrichmentOperation.CONFIRM; rationale="The evidence contains the existing claim's terms."
        else:
            operation=EnrichmentOperation.EXTEND; rationale="The evidence adds related information without replacing the claim."
        return KnowledgeEnrichmentProposal(claim.id or 0,fragment_id,operation,rationale)
