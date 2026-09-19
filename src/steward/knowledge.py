"""Canonical concepts and aliases, distinct from source files and summaries."""
from __future__ import annotations
import sqlite3
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from enum import StrEnum
import re
import json
from steward.activity import ActivityType

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


@dataclass(frozen=True, slots=True)
class ClaimRevision:
    action_proposal_id: int
    conflict_proposal_id: int
    original_claim_id: int
    replacement_claim_id: int
    created_at: datetime

class EnrichmentOperation(StrEnum):
    CONFIRM = "confirm"
    EXTEND = "extend"
    REFINE = "refine"
    QUALIFY = "qualify"
    CONTRADICT = "contradict"


class ConflictResolution(StrEnum):
    KEEP_EXISTING = "keep_existing"
    DISPUTED = "disputed"
    NEEDS_REVISION = "needs_revision"

@dataclass(frozen=True, slots=True)
class KnowledgeEnrichmentProposal:
    claim_id: int
    fragment_id: int
    operation: EnrichmentOperation
    rationale: str
    # Telegram-created reviews are tied to their origin chat.  Local CLI and
    # tool callers intentionally leave this empty; they do not inherit a
    # remote-chat capability merely because they share the operational DB.
    chat_id: str | None = None


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
    evidence_snapshot: str | None = None
    conflict_resolution: ConflictResolution | None = None
    conflict_resolved_at: datetime | None = None
    chat_id: str | None = None


class StaleKnowledgeReviewError(ValueError):
    """A review needs a fresh preview before its evidence can be accepted."""

    def __init__(self, proposal_id: int, claim_id: int, fragment_id: int) -> None:
        super().__init__("Claim or evidence changed, or this is a legacy review. Create a fresh enrichment proposal; this one remains pending.")
        self.proposal_id = proposal_id
        self.claim_id = claim_id
        self.fragment_id = fragment_id


class KnowledgeEnrichmentProposalRepository:
    """Persist proposed claim/evidence relationships without changing a claim."""

    def __init__(self, database_path: Path) -> None:
        self._database_path = database_path

    @staticmethod
    def _snapshot(connection: sqlite3.Connection, claim_id: int, fragment_id: int) -> str:
        row = connection.execute(
            "SELECT c.text, f.text, f.location, f.source_id, s.content_hash FROM claims c "
            "JOIN source_fragments f ON f.id = ? JOIN sources s ON s.id = f.source_id WHERE c.id = ?",
            (fragment_id, claim_id),
        ).fetchone()
        if row is None:
            raise ValueError("Claim or supporting evidence is unavailable.")
        return json.dumps(list(row), ensure_ascii=False)

    def add(self, proposal: KnowledgeEnrichmentProposal) -> StoredKnowledgeEnrichmentProposal:
        created_at = datetime.now(UTC)
        with sqlite3.connect(self._database_path) as connection:
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("BEGIN IMMEDIATE")
            snapshot = self._snapshot(connection, proposal.claim_id, proposal.fragment_id)
            connection.execute(
                """
                INSERT OR IGNORE INTO knowledge_enrichment_proposals
                (claim_id, fragment_id, operation, rationale, status, created_at, evidence_snapshot, chat_id)
                VALUES (?, ?, ?, ?, 'pending', ?, ?, ?)
                """,
                (
                    proposal.claim_id,
                    proposal.fragment_id,
                    proposal.operation.value,
                    proposal.rationale,
                    created_at.isoformat(),
                    snapshot,
                    proposal.chat_id,
                ),
            )
            row = connection.execute(
                """
                SELECT id, claim_id, fragment_id, operation, rationale, status, created_at, reviewed_at,
                       evidence_snapshot, conflict_resolution, conflict_resolved_at, chat_id
                FROM knowledge_enrichment_proposals
                WHERE claim_id = ? AND fragment_id = ? AND operation = ? AND rationale = ? AND evidence_snapshot = ?
                """,
                (proposal.claim_id, proposal.fragment_id, proposal.operation.value, proposal.rationale, snapshot),
            ).fetchone()
        if row is None:
            raise RuntimeError("Knowledge enrichment proposal was not persisted.")
        return self._from_row(row)

    def get(self, proposal_id: int) -> StoredKnowledgeEnrichmentProposal | None:
        with sqlite3.connect(self._database_path) as connection:
            row = connection.execute(
                """
                SELECT id, claim_id, fragment_id, operation, rationale, status, created_at, reviewed_at,
                       evidence_snapshot, conflict_resolution, conflict_resolved_at, chat_id
                FROM knowledge_enrichment_proposals WHERE id = ?
                """,
                (proposal_id,),
            ).fetchone()
        return self._from_row(row) if row else None

    def list_all(self) -> tuple[StoredKnowledgeEnrichmentProposal, ...]:
        with sqlite3.connect(self._database_path) as connection:
            rows = connection.execute(
                """
                SELECT id, claim_id, fragment_id, operation, rationale, status, created_at, reviewed_at,
                       evidence_snapshot, conflict_resolution, conflict_resolved_at, chat_id
                FROM knowledge_enrichment_proposals ORDER BY id
                """
            ).fetchall()
        return tuple(self._from_row(row) for row in rows)

    def review(self, proposal_id: int, status: str) -> StoredKnowledgeEnrichmentProposal:
        if status not in {"accepted", "rejected"}:
            raise ValueError("Knowledge enrichment status must be accepted or rejected.")
        reviewed_at = datetime.now(UTC)
        with sqlite3.connect(self._database_path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT id, claim_id, fragment_id, operation, rationale, status, created_at, reviewed_at, "
                "evidence_snapshot, conflict_resolution, conflict_resolved_at, chat_id "
                "FROM knowledge_enrichment_proposals WHERE id = ?", (proposal_id,),
            ).fetchone()
            if row is None or row[5] != "pending":
                raise ValueError("Knowledge enrichment proposal was not found or is already reviewed.")
            if status == "accepted":
                evidence = connection.execute(
                    "SELECT s.status FROM source_fragments f JOIN sources s ON s.id = f.source_id WHERE f.id = ?",
                    (row[2],),
                ).fetchone()
                if evidence != ("active",):
                    raise ValueError("The supporting source is no longer available; the proposal remains pending.")
                if row[8] is None or row[8] != self._snapshot(connection, int(row[1]), int(row[2])):
                    raise StaleKnowledgeReviewError(proposal_id, int(row[1]), int(row[2]))
            cursor = connection.execute(
                """
                UPDATE knowledge_enrichment_proposals
                SET status = ?, reviewed_at = ?
                WHERE id = ? AND status = 'pending'
                """,
                (status, reviewed_at.isoformat(), proposal_id),
            )
            connection.execute(
                "INSERT INTO activity_events (event_type, object_id, details, occurred_at) VALUES (?, ?, ?, ?)",
                (ActivityType.KNOWLEDGE_ENRICHMENT_ACCEPTED.value if status == "accepted" else ActivityType.KNOWLEDGE_ENRICHMENT_REJECTED.value,
                 str(proposal_id), f"{row[3]}: {row[4]}", reviewed_at.isoformat()),
            )
        if cursor.rowcount != 1:
            raise ValueError("Knowledge enrichment proposal was not found or is already reviewed.")
        proposal = self.get(proposal_id)
        if proposal is None:
            raise RuntimeError("Knowledge enrichment proposal disappeared after review.")
        return proposal

    def resolve_conflict(
        self, proposal_id: int, resolution: ConflictResolution | str
    ) -> StoredKnowledgeEnrichmentProposal:
        """Record the user's conclusion without rewriting either side of a conflict."""
        try:
            selected = ConflictResolution(resolution)
        except ValueError as error:
            raise ValueError(
                "Conflict resolution must be keep_existing, disputed, or needs_revision."
            ) from error
        resolved_at = datetime.now(UTC)
        with sqlite3.connect(self._database_path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT operation, status, conflict_resolution "
                "FROM knowledge_enrichment_proposals WHERE id = ?",
                (proposal_id,),
            ).fetchone()
            if row is None:
                raise ValueError("Knowledge conflict was not found.")
            if row[0] != EnrichmentOperation.CONTRADICT.value or row[1] != "accepted":
                raise ValueError("Only an accepted contradiction can be resolved.")
            if row[2] is not None:
                raise ValueError("Knowledge conflict is already resolved.")
            cursor = connection.execute(
                "UPDATE knowledge_enrichment_proposals "
                "SET conflict_resolution = ?, conflict_resolved_at = ? "
                "WHERE id = ? AND conflict_resolution IS NULL",
                (selected.value, resolved_at.isoformat(), proposal_id),
            )
            connection.execute(
                "INSERT INTO activity_events (event_type, object_id, details, occurred_at) "
                "VALUES (?, ?, ?, ?)",
                (
                    ActivityType.KNOWLEDGE_CONFLICT_RESOLVED.value,
                    str(proposal_id),
                    selected.value,
                    resolved_at.isoformat(),
                ),
            )
        if cursor.rowcount != 1:
            raise ValueError("Knowledge conflict is already resolved.")
        resolved = self.get(proposal_id)
        if resolved is None:
            raise RuntimeError("Knowledge conflict disappeared after resolution.")
        return resolved

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
            str(row[8]) if row[8] else None,
            ConflictResolution(str(row[9])) if row[9] else None,
            datetime.fromisoformat(str(row[10])) if row[10] else None,
            str(row[11]) if row[11] else None,
        )

class KnowledgeService:
    def __init__(self, database_path: Path) -> None: self._database_path = database_path

    def evidence_fragment_ids(self, claim_id: int) -> tuple[int, ...]:
        with sqlite3.connect(self._database_path) as connection:
            rows = connection.execute("SELECT fragment_id FROM claim_evidence WHERE claim_id = ? ORDER BY fragment_id", (claim_id,)).fetchall()
        return tuple(int(row[0]) for row in rows)

    def accepted_reviews(self, claim_id: int) -> tuple[StoredKnowledgeEnrichmentProposal, ...]:
        """Return current, available interpretations from one database read view."""
        with sqlite3.connect(self._database_path) as connection:
            rows = connection.execute(
                """SELECT p.id, p.claim_id, p.fragment_id, p.operation, p.rationale,
                          p.status, p.created_at, p.reviewed_at, p.evidence_snapshot,
                          p.conflict_resolution, p.conflict_resolved_at, p.chat_id,
                          c.text, f.text, f.location, f.source_id, s.content_hash
                   FROM knowledge_enrichment_proposals p
                   JOIN claims c ON c.id = p.claim_id
                   JOIN source_fragments f ON f.id = p.fragment_id
                   JOIN sources s ON s.id = f.source_id
                   WHERE p.claim_id = ? AND p.status = 'accepted'
                     AND s.status = 'active' AND p.evidence_snapshot IS NOT NULL
                   ORDER BY p.id""", (claim_id,),
            ).fetchall()
        return tuple(KnowledgeEnrichmentProposalRepository._from_row(row[:12])
                     for row in rows if row[8] == json.dumps(list(row[12:]), ensure_ascii=False))

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

    def list_claim_revisions(self, concept_id: int) -> tuple[ClaimRevision, ...]:
        """Return approved lineage while preserving both original and replacement claims."""
        with sqlite3.connect(self._database_path) as connection:
            rows = connection.execute(
                "SELECT r.action_proposal_id, r.conflict_proposal_id, r.original_claim_id, "
                "r.replacement_claim_id, r.created_at FROM claim_revisions r "
                "JOIN claims c ON c.id = r.original_claim_id WHERE c.concept_id = ? ORDER BY r.created_at, r.action_proposal_id",
                (concept_id,),
            ).fetchall()
        return tuple(
            ClaimRevision(
                int(row[0]), int(row[1]), int(row[2]), int(row[3]),
                datetime.fromisoformat(str(row[4])),
            )
            for row in rows
        )

    def accept_claim_revision(self, action_proposal_id: int) -> Claim:
        """Create a reviewed replacement claim and lineage in one SQLite transaction."""
        reviewed_at = datetime.now(UTC)
        with sqlite3.connect(self._database_path) as connection:
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("BEGIN IMMEDIATE")
            action = connection.execute(
                "SELECT action_type, payload_json, status FROM action_proposals WHERE id = ?",
                (action_proposal_id,),
            ).fetchone()
            if action is None or action[0] != "revise_knowledge_claim":
                raise ValueError("Claim revision proposal was not found.")
            if action[2] != "pending":
                raise ValueError("Claim revision proposal was already reviewed.")
            payload = json.loads(str(action[1]))
            try:
                conflict_id = int(payload["conflict_proposal_id"])
                original_claim_id = int(payload["claim_id"])
                fragment_id = int(payload["fragment_id"])
                replacement_text = " ".join(str(payload["replacement_text"]).split())
            except (KeyError, TypeError, ValueError) as error:
                raise ValueError("Claim revision proposal is malformed.") from error
            if not replacement_text or len(replacement_text) > 2_000:
                raise ValueError("Replacement claim text must contain between 1 and 2,000 characters.")
            conflict = connection.execute(
                "SELECT p.claim_id, p.fragment_id, p.operation, p.status, p.conflict_resolution, "
                "p.evidence_snapshot, c.concept_id, c.text "
                "FROM knowledge_enrichment_proposals p JOIN claims c ON c.id = p.claim_id "
                "WHERE p.id = ?",
                (conflict_id,),
            ).fetchone()
            if conflict is None or int(conflict[0]) != original_claim_id or int(conflict[1]) != fragment_id:
                raise ValueError("Claim revision no longer matches its reviewed conflict.")
            if (
                conflict[2] != EnrichmentOperation.CONTRADICT.value
                or conflict[3] != "accepted"
                or conflict[4] != ConflictResolution.NEEDS_REVISION.value
            ):
                raise ValueError("Claim revision requires an accepted conflict marked as needing revision.")
            if replacement_text.casefold() == str(conflict[7]).strip().casefold():
                raise ValueError("Replacement claim text must differ from the original claim.")
            evidence = connection.execute(
                "SELECT s.status FROM source_fragments f JOIN sources s ON s.id = f.source_id WHERE f.id = ?",
                (fragment_id,),
            ).fetchone()
            if evidence != ("active",):
                raise ValueError("The conflict evidence is no longer available; the revision remains pending.")
            if conflict[5] is None or conflict[5] != KnowledgeEnrichmentProposalRepository._snapshot(
                connection, original_claim_id, fragment_id
            ):
                raise ValueError("The claim or evidence changed; create a fresh conflict review before revising.")
            if connection.execute(
                "SELECT 1 FROM claim_revisions WHERE conflict_proposal_id = ?", (conflict_id,)
            ).fetchone():
                raise ValueError("This knowledge conflict already has an accepted replacement claim.")
            cursor = connection.execute(
                "INSERT INTO claims (concept_id, text, created_at) VALUES (?, ?, ?)",
                (int(conflict[6]), replacement_text, reviewed_at.isoformat()),
            )
            replacement_claim_id = int(cursor.lastrowid)
            connection.execute(
                "INSERT INTO claim_evidence (claim_id, fragment_id) VALUES (?, ?)",
                (replacement_claim_id, fragment_id),
            )
            connection.execute(
                "INSERT INTO claim_revisions (action_proposal_id, conflict_proposal_id, original_claim_id, "
                "replacement_claim_id, created_at) VALUES (?, ?, ?, ?, ?)",
                (action_proposal_id, conflict_id, original_claim_id, replacement_claim_id, reviewed_at.isoformat()),
            )
            updated = connection.execute(
                "UPDATE action_proposals SET status = 'accepted', reviewed_at = ? "
                "WHERE id = ? AND status = 'pending'",
                (reviewed_at.isoformat(), action_proposal_id),
            )
            if updated.rowcount != 1:
                raise ValueError("Claim revision proposal was already reviewed.")
            connection.executemany(
                "INSERT INTO activity_events (event_type, object_id, details, occurred_at) VALUES (?, ?, ?, ?)",
                (
                    (
                        ActivityType.KNOWLEDGE_CLAIM_REVISED.value,
                        str(replacement_claim_id),
                        f"replaces claim:{original_claim_id}; conflict:{conflict_id}",
                        reviewed_at.isoformat(),
                    ),
                    (
                        ActivityType.ACTION_ACCEPTED.value,
                        str(action_proposal_id),
                        "revise_knowledge_claim",
                        reviewed_at.isoformat(),
                    ),
                ),
            )
        replacement = self.get_claim(replacement_claim_id)
        if replacement is None:
            raise RuntimeError("Replacement claim disappeared after acceptance.")
        return replacement
    def compare_evidence(self, claim: Claim, *, fragment_id: int, evidence_text: str) -> KnowledgeEnrichmentProposal:
        def words(text: str) -> set[str]:
            return {
                token[:-1] if len(token) > 3 and token.endswith("s") else token
                for token in re.findall(r"\w+", text.casefold())
            }

        claim_words=words(claim.text); evidence_words=words(evidence_text)
        ignored = {"a", "an", "the", "and", "or", "is", "are", "of", "to", "in", "for", "that", "this"}
        claim_terms = claim_words - ignored
        overlap_needed = 1 if len(claim_terms) <= 1 else 2
        sentences = [words(sentence) for sentence in re.split(r"[.!?]+", evidence_text)]

        def refers_to_claim(markers: set[str], minimum_overlap: int = overlap_needed) -> bool:
            return any(
                markers & sentence and len(claim_terms & sentence) >= minimum_overlap
                for sentence in sentences
            )

        if refers_to_claim({"not", "never", "false", "incorrect"}):
            operation=EnrichmentOperation.CONTRADICT; rationale="The evidence explicitly negates terms shared with the claim."
        elif refers_to_claim({"may", "might", "sometimes", "usually", "typically", "depends"}):
            operation=EnrichmentOperation.QUALIFY; rationale="The evidence limits the scope or certainty of shared claim terms."
        elif refers_to_claim({"instead", "revised", "updated", "replace"}, 1):
            operation=EnrichmentOperation.REFINE; rationale="The evidence indicates a more specific or revised formulation of shared claim terms."
        elif claim_words <= evidence_words:
            operation=EnrichmentOperation.CONFIRM; rationale="The evidence contains the existing claim's terms."
        else:
            operation=EnrichmentOperation.EXTEND; rationale="The evidence adds related information without replacing the claim."
        return KnowledgeEnrichmentProposal(claim.id or 0,fragment_id,operation,rationale)
