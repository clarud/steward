from pathlib import Path
import sqlite3
import pytest
from steward.knowledge import Claim, EnrichmentOperation, KnowledgeService
from steward.knowledge_ai import ModelAssistedKnowledgeService
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
