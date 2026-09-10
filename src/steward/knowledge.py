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


@dataclass(frozen=True, slots=True)
class StoredKnowledgeEnrichmentProposal:
    """A reviewable enrichment interpretation, always linked to original evidence."""

    id: int
    claim_id: int
    fragment_id: int
    operation: EnrichmentOperation
    rationale: str
    status: str
    created_at: datetime
    reviewed_at: datetime | None


class KnowledgeEnrichmentProposalRepository:
    """Persist proposed claim/evidence relationships without changing a claim."""

    def __init__(self, database_path: Path) -> None:
        self._database_path = database_path

    def add(self, proposal: KnowledgeEnrichmentProposal) -> StoredKnowledgeEnrichmentProposal:
        created_at = datetime.now(UTC)
        with sqlite3.connect(self._database_path) as connection:
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute(
                """
                INSERT OR IGNORE INTO knowledge_enrichment_proposals
                (claim_id, fragment_id, operation, rationale, status, created_at)
                VALUES (?, ?, ?, ?, 'pending', ?)
                """,
                (
                    proposal.claim_id,
                    proposal.fragment_id,
                    proposal.operation.value,
                    proposal.rationale,
                    created_at.isoformat(),
                ),
            )
            row = connection.execute(
                """
                SELECT id, claim_id, fragment_id, operation, rationale, status, created_at, reviewed_at
                FROM knowledge_enrichment_proposals
                WHERE claim_id = ? AND fragment_id = ? AND operation = ? AND rationale = ?
                """,
                (proposal.claim_id, proposal.fragment_id, proposal.operation.value, proposal.rationale),
            ).fetchone()
        if row is None:
            raise RuntimeError("Knowledge enrichment proposal was not persisted.")
        return self._from_row(row)

    def get(self, proposal_id: int) -> StoredKnowledgeEnrichmentProposal | None:
        with sqlite3.connect(self._database_path) as connection:
            row = connection.execute(
                """
                SELECT id, claim_id, fragment_id, operation, rationale, status, created_at, reviewed_at
                FROM knowledge_enrichment_proposals WHERE id = ?
                """,
                (proposal_id,),
            ).fetchone()
        return self._from_row(row) if row else None

    def list_all(self) -> tuple[StoredKnowledgeEnrichmentProposal, ...]:
        with sqlite3.connect(self._database_path) as connection:
            rows = connection.execute(
                """
                SELECT id, claim_id, fragment_id, operation, rationale, status, created_at, reviewed_at
                FROM knowledge_enrichment_proposals ORDER BY id
                """
            ).fetchall()
        return tuple(self._from_row(row) for row in rows)

    def review(self, proposal_id: int, status: str) -> StoredKnowledgeEnrichmentProposal:
        if status not in {"accepted", "rejected"}:
            raise ValueError("Knowledge enrichment status must be accepted or rejected.")
        reviewed_at = datetime.now(UTC)
        with sqlite3.connect(self._database_path) as connection:
            cursor = connection.execute(
                """
                UPDATE knowledge_enrichment_proposals
                SET status = ?, reviewed_at = ?
                WHERE id = ? AND status = 'pending'
                """,
                (status, reviewed_at.isoformat(), proposal_id),
            )
        if cursor.rowcount != 1:
            raise ValueError("Knowledge enrichment proposal was not found or is already reviewed.")
        proposal = self.get(proposal_id)
        if proposal is None:
            raise RuntimeError("Knowledge enrichment proposal disappeared after review.")
        return proposal

    @staticmethod
    def _from_row(row: tuple[object, ...]) -> StoredKnowledgeEnrichmentProposal:
        return StoredKnowledgeEnrichmentProposal(
            int(row[0]),
            int(row[1]),
            int(row[2]),
            EnrichmentOperation(str(row[3])),
            str(row[4]),
            str(row[5]),
            datetime.fromisoformat(str(row[6])),
            datetime.fromisoformat(str(row[7])) if row[7] else None,
        )

class KnowledgeService:
    def __init__(self, database_path: Path) -> None: self._database_path = database_path

    def evidence_fragment_ids(self, claim_id: int) -> tuple[int, ...]:
        with sqlite3.connect(self._database_path) as connection:
            rows = connection.execute("SELECT fragment_id FROM claim_evidence WHERE claim_id = ? ORDER BY fragment_id", (claim_id,)).fetchall()
        return tuple(int(row[0]) for row in rows)

    def accepted_reviews(self, claim_id: int) -> tuple[StoredKnowledgeEnrichmentProposal, ...]:
        return tuple(item for item in KnowledgeEnrichmentProposalRepository(self._database_path).list_all() if item.claim_id == claim_id and item.status == "accepted")

    def create_concept(self, name: str) -> Concept:
        concept = Concept(None, name.strip(), datetime.now(UTC))
        if not concept.name: raise ValueError("Concept name must not be empty.")
        with sqlite3.connect(self._database_path) as connection:
            connection.execute("PRAGMA foreign_keys = ON")
            cursor=connection.execute("INSERT INTO concepts (name,created_at) VALUES (?,?)",(concept.name,concept.created_at.isoformat()))
        return replace(concept,id=int(cursor.lastrowid))

    def list_concepts(self) -> tuple[Concept, ...]:
        with sqlite3.connect(self._database_path) as connection:
            rows = connection.execute("SELECT id, name, created_at FROM concepts ORDER BY name COLLATE NOCASE, id").fetchall()
        return tuple(Concept(int(row[0]), str(row[1]), datetime.fromisoformat(str(row[2]))) for row in rows)
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
    def get_claim(self, claim_id: int) -> Claim | None:
        with sqlite3.connect(self._database_path) as connection:
            row = connection.execute(
                "SELECT id, concept_id, text, created_at FROM claims WHERE id = ?", (claim_id,)
            ).fetchone()
        return Claim(int(row[0]), int(row[1]), str(row[2]), datetime.fromisoformat(str(row[3]))) if row else None

    def list_claims(self, concept_id: int) -> tuple[Claim, ...]:
        """Return canonical claims for one concept without synthesizing new knowledge."""

        with sqlite3.connect(self._database_path) as connection:
            rows = connection.execute(
                "SELECT id, concept_id, text, created_at FROM claims WHERE concept_id = ? ORDER BY id",
                (concept_id,),
            ).fetchall()
        return tuple(
            Claim(int(row[0]), int(row[1]), str(row[2]), datetime.fromisoformat(str(row[3])))
            for row in rows
        )
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
