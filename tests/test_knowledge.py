from pathlib import Path
import json
import sqlite3
import pytest
from steward.activity import ActivityService
from steward.action_proposals import ActionProposalRepository
from steward.knowledge import (
    Claim,
    ConflictResolution,
    EnrichmentOperation,
    KnowledgeEnrichmentProposalRepository,
    KnowledgeService,
)
from steward.knowledge_ai import ModelAssistedKnowledgeService
from steward.tools import KnowledgeProposalToolService, build_knowledge_proposal_tools
from steward.storage import initialize_database
from steward.sources import Source, SourceRepository, SourceType
from steward.extraction import ExtractionResult, SourceFragment, SourceFragmentRepository
from datetime import UTC, datetime

def test_concept_and_alias_are_persisted(tmp_path: Path) -> None:
    database=tmp_path / "db.sqlite"; initialize_database(database)
    service=KnowledgeService(database); concept=service.create_concept("Translation Lookaside Buffer")
    service.add_alias(concept.id or 0,"TLB"); service.add_alias(concept.id or 0,"TLB")
    assert concept.id == 1
    assert service.find("TLB") == concept

def test_concept_proposal_keeps_evidence_ids_without_persisting_new_names(tmp_path: Path) -> None:
    database=tmp_path / "db.sqlite"; initialize_database(database)
    service=KnowledgeService(database); concept=service.create_concept("TLB")
    proposal=service.propose(["TLB", "Page Table"], [4, 9])
    assert proposal.existing_matches == (concept.id,) and proposal.new_concepts == ("Page Table",)

def test_claim_requires_persisted_fragment_evidence(tmp_path: Path) -> None:
    database=tmp_path / "db.sqlite"; initialize_database(database); timestamp=datetime(2026,9,8,tzinfo=UTC)
    source=SourceRepository(database).add(Source(None,tmp_path / "note.md","a" * 64,SourceType.MARKDOWN,0,timestamp,timestamp,timestamp))
    fragment=SourceFragmentRepository(database).replace_for_source(ExtractionResult(source.id or 0,(SourceFragment(None,source.id or 0,None,0,"TLBs cache translations.","lines 1-1"),)))[0]
    service=KnowledgeService(database); concept=service.create_concept("TLB")
    claim=service.create_claim(concept.id or 0,"A TLB caches translations.",[fragment.id or 0])
    assert claim.id == 1

def test_enrichment_proposal_is_derived_and_does_not_change_claim(tmp_path: Path) -> None:
    database=tmp_path / "db.sqlite"; initialize_database(database); service=KnowledgeService(database)
    claim=Claim(1, 1, "TLB caches translations.", datetime(2026, 9, 8, tzinfo=UTC))
    proposal=service.compare_evidence(claim,fragment_id=2,evidence_text="A TLB caches translations and speeds up lookup.")
    assert proposal.operation.value == "confirm" and claim.text == "TLB caches translations."


def test_enrichment_proposal_is_durable_and_requires_one_explicit_review(tmp_path: Path) -> None:
    database = tmp_path / "db.sqlite"; initialize_database(database)
    timestamp = datetime(2026, 9, 8, tzinfo=UTC)
    source = SourceRepository(database).add(
        Source(None, tmp_path / "note.md", "b" * 64, SourceType.MARKDOWN, 0, timestamp, timestamp, timestamp)
    )
    fragment = SourceFragmentRepository(database).replace_for_source(
        ExtractionResult(source.id or 0, (SourceFragment(None, source.id or 0, None, 0, "TLBs may cache translations.", "lines 1-1"),))
    )[0]
    knowledge = KnowledgeService(database)
    concept = knowledge.create_concept("TLB")
    claim = knowledge.create_claim(concept.id or 0, "TLBs cache translations.", [fragment.id or 0])
    derived = knowledge.compare_evidence(claim, fragment_id=fragment.id or 0, evidence_text=fragment.text)
    repository = KnowledgeEnrichmentProposalRepository(database)

    stored = repository.add(derived)
    with sqlite3.connect(database) as connection:
        connection.execute("""CREATE TRIGGER fail_enrichment_audit BEFORE INSERT ON activity_events
            BEGIN SELECT RAISE(ABORT, 'injected review failure'); END""")
    with pytest.raises(sqlite3.IntegrityError, match="injected review failure"):
        repository.review(stored.id, "accepted")
    assert repository.get(stored.id).status == "pending"
    with sqlite3.connect(database) as connection:
        connection.execute("DROP TRIGGER fail_enrichment_audit")
        connection.execute("UPDATE sources SET status = 'missing' WHERE id = ?", (source.id,))
    with pytest.raises(ValueError, match="no longer available"):
        repository.review(stored.id, "accepted")
    assert repository.get(stored.id).status == "pending"
    with sqlite3.connect(database) as connection:
        connection.execute("UPDATE sources SET status = 'active' WHERE id = ?", (source.id,))
    reviewed = repository.review(stored.id, "accepted")

    assert stored.status == "pending"
    assert reviewed.status == "accepted"
    assert reviewed.claim_id == claim.id
    assert reviewed.fragment_id == fragment.id
    assert repository.add(derived).id == stored.id
    with pytest.raises(ValueError, match="already reviewed"):
        repository.review(stored.id, "rejected")
    events = ActivityService(database).list_recent()
    assert len(events) == 1
    assert events[0].event_type.value == "knowledge_enrichment_accepted"


@pytest.mark.parametrize("resolution", tuple(ConflictResolution))
def test_accepted_conflict_requires_an_explicit_non_mutating_resolution(
    tmp_path: Path, resolution: ConflictResolution
) -> None:
    database = tmp_path / "db.sqlite"; initialize_database(database)
    now = datetime(2026, 9, 13, tzinfo=UTC)
    source = SourceRepository(database).add(
        Source(None, tmp_path / "note.md", "c" * 64, SourceType.MARKDOWN, 0, now, now, now)
    )
    fragment = SourceFragmentRepository(database).replace_for_source(ExtractionResult(source.id or 0, (
        SourceFragment(None, source.id or 0, None, 0, "Queues do not buffer jobs.", "line 1"),
    )))[0]
    knowledge = KnowledgeService(database)
    concept = knowledge.create_concept("Queues")
    claim = knowledge.create_claim(concept.id or 0, "Queues buffer jobs.", [fragment.id or 0])
    repository = KnowledgeEnrichmentProposalRepository(database)
    pending = repository.add(knowledge.compare_evidence(
        claim, fragment_id=fragment.id or 0, evidence_text=fragment.text
    ))
    accepted = repository.review(pending.id, "accepted")

    resolved = repository.resolve_conflict(accepted.id, resolution)

    assert resolved.conflict_resolution is resolution
    assert resolved.conflict_resolved_at is not None
    assert knowledge.get_claim(claim.id or 0) == claim
    with pytest.raises(ValueError, match="already resolved"):
        repository.resolve_conflict(accepted.id, resolution)
    assert ActivityService(database).list_recent()[0].event_type.value == "knowledge_conflict_resolved"


def test_reviewed_claim_revision_creates_evidence_lineage_without_overwriting(
    tmp_path: Path,
) -> None:
    database = tmp_path / "db.sqlite"; initialize_database(database)
    now = datetime(2026, 9, 13, tzinfo=UTC)
    source = SourceRepository(database).add(Source(
        None, tmp_path / "note.md", "e" * 64, SourceType.MARKDOWN, 0, now, now, now
    ))
    fragment = SourceFragmentRepository(database).replace_for_source(ExtractionResult(source.id or 0, (
        SourceFragment(None, source.id or 0, None, 0, "Queues do not always buffer jobs.", "line 1"),
    )))[0]
    knowledge = KnowledgeService(database)
    concept = knowledge.create_concept("Queues")
    original = knowledge.create_claim(concept.id or 0, "Queues always buffer jobs.", [fragment.id or 0])
    conflicts = KnowledgeEnrichmentProposalRepository(database)
    conflict = conflicts.add(knowledge.compare_evidence(
        original, fragment_id=fragment.id or 0, evidence_text="Queues do not always buffer jobs."
    ))
    conflicts.review(conflict.id, "accepted")
    conflicts.resolve_conflict(conflict.id, ConflictResolution.NEEDS_REVISION)
    actions = ActionProposalRepository(database)
    action = actions.add("revise_knowledge_claim", {
        "conflict_proposal_id": str(conflict.id),
        "claim_id": str(original.id),
        "fragment_id": str(fragment.id),
        "replacement_text": "Queues may buffer jobs depending on their implementation.",
    })

    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TRIGGER fail_claim_revision_audit BEFORE INSERT ON activity_events "
            "WHEN NEW.event_type = 'knowledge_claim_revised' "
            "BEGIN SELECT RAISE(ABORT, 'injected revision audit failure'); END"
        )
    with pytest.raises(sqlite3.IntegrityError, match="injected revision audit failure"):
        knowledge.accept_claim_revision(action.id or 0)
    assert knowledge.list_claims(concept.id or 0) == (original,)
    assert knowledge.list_claim_revisions(concept.id or 0) == ()
    assert actions.get(action.id or 0).status == "pending"
    with sqlite3.connect(database) as connection:
        connection.execute("DROP TRIGGER fail_claim_revision_audit")
        connection.execute("UPDATE sources SET status = 'missing' WHERE id = ?", (source.id,))
    with pytest.raises(ValueError, match="no longer available"):
        knowledge.accept_claim_revision(action.id or 0)
    assert actions.get(action.id or 0).status == "pending"
    with sqlite3.connect(database) as connection:
        connection.execute("UPDATE sources SET status = 'active' WHERE id = ?", (source.id,))

    replacement = knowledge.accept_claim_revision(action.id or 0)

    assert knowledge.get_claim(original.id or 0) == original
    assert replacement.text == "Queues may buffer jobs depending on their implementation."
    assert knowledge.evidence_fragment_ids(replacement.id or 0) == (fragment.id,)
    assert knowledge.list_claim_revisions(concept.id or 0)[0].original_claim_id == original.id
    assert knowledge.list_claim_revisions(concept.id or 0)[0].replacement_claim_id == replacement.id
    assert actions.get(action.id or 0).status == "accepted"
    assert [event.event_type.value for event in ActivityService(database).list_recent()[:2]] == [
        "action_accepted", "knowledge_claim_revised",
    ]


def test_claim_requires_existing_evidence_and_concept(tmp_path: Path) -> None:
    database = tmp_path / "db.sqlite"; initialize_database(database)

    with pytest.raises(sqlite3.IntegrityError):
        KnowledgeService(database).create_claim(99, "Unsupported claim", [42])


@pytest.mark.parametrize("change", ["claim", "fragment", "location", "hash"])
def test_enrichment_snapshot_rejects_changes_and_allows_fresh_review(tmp_path: Path, change: str) -> None:
    database = tmp_path / "db.sqlite"
    initialize_database(database)
    now = datetime.now(UTC)
    source = SourceRepository(database).add(Source(None, tmp_path / "note.md", "a" * 64, SourceType.MARKDOWN, 0, now, now, now))
    fragment = SourceFragmentRepository(database).replace_for_source(ExtractionResult(source.id, (
        SourceFragment(None, source.id, None, 0, "Queues buffer jobs.", "line 1"),
    )))[0]
    knowledge = KnowledgeService(database)
    concept = knowledge.create_concept("Queues")
    claim = knowledge.create_claim(concept.id, "Queues buffer jobs.", [fragment.id])
    repository = KnowledgeEnrichmentProposalRepository(database)
    derived = knowledge.compare_evidence(claim, fragment_id=fragment.id, evidence_text=fragment.text)
    old = repository.add(derived)
    statements = {
        "claim": "UPDATE claims SET text = 'Queues buffer some jobs.'",
        "fragment": "UPDATE source_fragments SET text = 'Queues may buffer jobs.'",
        "location": "UPDATE source_fragments SET location = 'line 2'",
        "hash": "UPDATE sources SET content_hash = 'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb'",
    }
    with sqlite3.connect(database) as connection:
        connection.execute(statements[change])
    with pytest.raises(ValueError, match="fresh enrichment"):
        repository.review(old.id, "accepted")
    assert repository.get(old.id).status == "pending"
    fresh = repository.add(derived)
    assert fresh.id != old.id
    assert repository.add(derived).id == fresh.id
    repository.review(fresh.id, "accepted")
    assert knowledge.accepted_reviews(claim.id) == (repository.get(fresh.id),)
    with sqlite3.connect(database) as connection:
        connection.execute("UPDATE sources SET status = 'missing' WHERE id = ?", (source.id,))
    assert knowledge.accepted_reviews(claim.id) == ()
    assert repository.get(fresh.id).status == "accepted"
    with sqlite3.connect(database) as connection:
        connection.execute("UPDATE sources SET status = 'active' WHERE id = ?", (source.id,))
    assert knowledge.accepted_reviews(claim.id) == (repository.get(fresh.id),)
    with sqlite3.connect(database) as connection:
        connection.execute("UPDATE claims SET text = 'Changed after review'")
    assert knowledge.accepted_reviews(claim.id) == ()
    assert repository.get(fresh.id).status == "accepted"
    with sqlite3.connect(database) as connection:
        # Simulate a legacy orphan without foreign-key enforcement. Lookup must
        # degrade safely rather than raising while examining the stale review.
        connection.execute("DELETE FROM source_fragments WHERE id = ?", (fragment.id,))
    assert knowledge.accepted_reviews(claim.id) == ()


def test_legacy_enrichment_upgrade_preserves_reviews_without_inventing_snapshots(tmp_path: Path, monkeypatch) -> None:
    from steward.storage import database as schema
    database = tmp_path / "legacy.sqlite"
    with monkeypatch.context() as patch:
        patch.setattr(schema, "MIGRATIONS", tuple(item for item in schema.MIGRATIONS if item[0] < 46))
        initialize_database(database)
    now = datetime.now(UTC)
    source = SourceRepository(database).add(Source(None, tmp_path / "note.md", "a" * 64, SourceType.MARKDOWN, 0, now, now, now))
    fragment = SourceFragmentRepository(database).replace_for_source(ExtractionResult(source.id, (
        SourceFragment(None, source.id, None, 0, "Queues buffer jobs.", "line 1"),
    )))[0]
    knowledge = KnowledgeService(database)
    concept = knowledge.create_concept("Queues")
    claim = knowledge.create_claim(concept.id, "Queues buffer jobs.", [fragment.id])
    with sqlite3.connect(database) as connection:
        connection.execute("INSERT INTO knowledge_enrichment_proposals (claim_id,fragment_id,operation,rationale,status,created_at) VALUES (?,?,'confirm','Review','pending',?)",
                           (claim.id, fragment.id, now.isoformat()))
    initialize_database(database)
    initialize_database(database)
    repository = KnowledgeEnrichmentProposalRepository(database)
    assert repository.get(1).evidence_snapshot is None
    with pytest.raises(ValueError, match="legacy review"):
        repository.review(1, "accepted")
    repository.review(1, "rejected")
    assert repository.get(1).status == "rejected"


@pytest.mark.parametrize(
    ("evidence", "expected"),
    [
        ("TLBs do not cache translations.", EnrichmentOperation.CONTRADICT),
        ("TLBs may cache translations depending on the architecture.", EnrichmentOperation.QUALIFY),
        ("A revised TLB design replaces the old cache strategy.", EnrichmentOperation.REFINE),
        ("TLBs also improve latency.", EnrichmentOperation.EXTEND),
    ],
)
def test_enrichment_can_propose_each_non_confirming_operation(tmp_path: Path, evidence: str, expected: EnrichmentOperation) -> None:
    service = KnowledgeService(tmp_path / "unused.db")
    claim = Claim(1, 1, "TLB caches translations.", datetime(2026, 9, 8, tzinfo=UTC))

    assert service.compare_evidence(claim, fragment_id=2, evidence_text=evidence).operation is expected


def test_unrelated_negation_does_not_create_a_conflict_proposal(tmp_path: Path) -> None:
    service = KnowledgeService(tmp_path / "unused.db")
    claim = Claim(1, 1, "TLB caches translations.", datetime(2026, 9, 8, tzinfo=UTC))

    proposal = service.compare_evidence(
        claim, fragment_id=2,
        evidence_text="This note does not discuss performance measurements. TLB caches translations.",
    )

    assert proposal.operation is EnrichmentOperation.CONFIRM


def test_model_assisted_enrichment_validates_a_grounded_structured_proposal() -> None:
    class Model:
        def generate(self, **kwargs):
            assert "TLBs may cache" in kwargs["input_text"]
            return '{"operation":"qualify","rationale":"The evidence limits the claim to some architectures."}'

    service = KnowledgeService(Path("unused.db"))
    claim = Claim(1, 1, "TLBs cache translations.", datetime(2026, 9, 8, tzinfo=UTC))
    fragment = SourceFragment(2, 1, None, 0, "TLBs may cache translations depending on the architecture.", "lines 1-1")

    proposal = ModelAssistedKnowledgeService(Model(), fallback=service).compare_evidence(claim, fragment)

    assert proposal.operation is EnrichmentOperation.QUALIFY
    assert proposal.rationale == "The evidence limits the claim to some architectures."


def test_knowledge_proposal_tool_creates_a_reviewable_item_only(tmp_path: Path) -> None:
    database = tmp_path / "db.sqlite"; initialize_database(database)
    timestamp = datetime(2026, 9, 8, tzinfo=UTC)
    source = SourceRepository(database).add(
        Source(None, tmp_path / "note.md", "d" * 64, SourceType.MARKDOWN, 0, timestamp, timestamp, timestamp)
    )
    fragment = SourceFragmentRepository(database).replace_for_source(
        ExtractionResult(source.id or 0, (SourceFragment(None, source.id or 0, None, 0, "TLBs may cache translations.", "lines 1-1"),))
    )[0]
    knowledge = KnowledgeService(database)
    concept = knowledge.create_concept("TLB")
    claim = knowledge.create_claim(concept.id or 0, "TLBs cache translations.", [fragment.id or 0])
    tool_service = KnowledgeProposalToolService(
        knowledge,
        SourceFragmentRepository(database),
        KnowledgeEnrichmentProposalRepository(database),
        ActivityService(database),
    )

    result = json.loads(tool_service.propose_knowledge_enrichment(claim.id or 0, fragment.id or 0))

    assert [tool.name for tool in build_knowledge_proposal_tools(tool_service)] == ["propose_knowledge_enrichment"]
    assert result["status"] == "pending"
    assert result["claim_id"] == claim.id
    assert knowledge.get_claim(claim.id or 0) == claim
