from pathlib import Path
from steward.knowledge import KnowledgeService
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
