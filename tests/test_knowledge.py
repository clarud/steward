from pathlib import Path
import json
import sqlite3
import pytest
from steward.activity import ActivityService
from steward.knowledge import (
    Claim,
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
    reviewed = repository.review(stored.id, "accepted")

    assert stored.status == "pending"
    assert reviewed.status == "accepted"
    assert reviewed.claim_id == claim.id
    assert reviewed.fragment_id == fragment.id
    assert repository.add(derived).id == stored.id
    with pytest.raises(ValueError, match="already reviewed"):
        repository.review(stored.id, "rejected")


def test_claim_requires_existing_evidence_and_concept(tmp_path: Path) -> None:
    database = tmp_path / "db.sqlite"; initialize_database(database)

    with pytest.raises(sqlite3.IntegrityError):
        KnowledgeService(database).create_claim(99, "Unsupported claim", [42])


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
