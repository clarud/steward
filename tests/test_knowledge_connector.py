from datetime import UTC, datetime
from pathlib import Path

from steward.extraction import ExtractionResult, SourceFragment, SourceFragmentRepository
from steward.knowledge import KnowledgeService
from steward.knowledge_connector import KnowledgeConnector
from steward.sources import Source, SourceRepository, SourceType
from steward.storage import initialize_database


def test_connector_proposes_only_evidence_backed_cross_concept_links(tmp_path: Path) -> None:
    database = tmp_path / "steward.db"; initialize_database(database)
    now = datetime(2026, 9, 8, tzinfo=UTC)
    source = SourceRepository(database).add(Source(None, tmp_path / "note.md", "a" * 64, SourceType.MARKDOWN, 0, now, now, now))
    fragment = SourceFragmentRepository(database).replace_for_source(
        ExtractionResult(source.id or 0, (SourceFragment(None, source.id or 0, None, 0, "TLBs and page tables work together.", "lines 1"),))
    )[0]
    knowledge = KnowledgeService(database)
    tlb = knowledge.create_concept("TLB"); page_tables = knowledge.create_concept("Page Tables")
    knowledge.create_claim(tlb.id or 0, "TLBs cache translations.", [fragment.id or 0])
    knowledge.create_claim(page_tables.id or 0, "Page tables map addresses.", [fragment.id or 0])

    proposals = KnowledgeConnector(database).propose()

    assert len(proposals) == 1
    assert proposals[0].supporting_fragment_ids == (fragment.id,)
    assert "not" in proposals[0].where_analogy_breaks
