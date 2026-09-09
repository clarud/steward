import json
from datetime import UTC, datetime
from pathlib import Path

from steward.activity import ActivityService, ActivityType
from steward.extraction import ExtractionResult, SourceFragment, SourceFragmentRepository
from steward.knowledge import KnowledgeService
from steward.privacy import PrivacyRule, PrivacyService
from steward.records import (
    ReceiptRecord,
    ReceiptRecordProposal,
    RecordService,
    WarrantyRecord,
    WarrantyRecordProposal,
)
from steward.retrieval import LexicalSearchService
from steward.sources import Source, SourceRepository, SourceType
from steward.storage import initialize_database
from steward.tools import ReadOnlyToolService, build_read_only_tools
from steward.workspaces import WorkspaceRepository


def test_read_only_tools_return_provenance_without_changing_domain_state(tmp_path: Path) -> None:
    database = tmp_path / "steward.db"
    initialize_database(database)
    now = datetime(2026, 9, 8, tzinfo=UTC)
    sources = SourceRepository(database)
    source = sources.add(Source(None, tmp_path / "tlb.md", "a" * 64, SourceType.MARKDOWN, 1, now, now, now))
    fragments = SourceFragmentRepository(database)
    stored = fragments.replace_for_source(
        ExtractionResult(source.id or 0, (SourceFragment(None, source.id or 0, "TLB", 0, "TLBs cache translations.", "lines 1-2"),))
    )[0]
    knowledge = KnowledgeService(database)
    concept = knowledge.create_concept("Translation Lookaside Buffer")
    knowledge.add_alias(concept.id or 0, "TLB")
    workspaces = WorkspaceRepository(database)
    workspaces.create("Operating Systems")
    activity = ActivityService(database)
    activity.record(ActivityType.SOURCE_CAPTURED, object_id=str(source.id), details="tlb.md")
    service = ReadOnlyToolService(
        sources, fragments, LexicalSearchService(sources, fragments), knowledge,
        RecordService(database), workspaces, activity,
    )

    source_hits = json.loads(service.search_sources("translations"))
    read = json.loads(service.read_source(source.id or 0))
    found_concept = json.loads(service.search_knowledge("TLB"))
    found_workspaces = json.loads(service.search_workspaces("operating"))
    events = json.loads(service.search_activity("captured"))

    assert source_hits[0]["fragment_id"] == stored.id
    assert read["fragments"][0]["text"] == "TLBs cache translations."
    assert found_concept["concept"]["id"] == concept.id
    assert found_workspaces[0]["name"] == "Operating Systems"
    assert events[0]["event_type"] == "source_captured"
    assert len(sources.list_all()) == 1
    assert len(activity.list_recent()) == 1


def test_read_only_tools_expose_only_the_phase_21_safe_tool_set(tmp_path: Path) -> None:
    database = tmp_path / "steward.db"
    initialize_database(database)
    sources = SourceRepository(database)
    fragments = SourceFragmentRepository(database)
    service = ReadOnlyToolService(
        sources, fragments, LexicalSearchService(sources, fragments), KnowledgeService(database),
        RecordService(database), WorkspaceRepository(database), ActivityService(database),
    )

    assert [tool.name for tool in build_read_only_tools(service)] == [
        "search_sources", "read_source", "search_knowledge", "search_records",
        "search_workspaces", "search_activity",
    ]


def test_read_only_record_search_includes_receipts_and_warranties(tmp_path: Path) -> None:
    database = tmp_path / "steward.db"; initialize_database(database)
    now = datetime(2026, 9, 8, tzinfo=UTC)
    sources = SourceRepository(database)
    source = sources.add(Source(None, tmp_path / "records.txt", "a" * 64, SourceType.PLAIN_TEXT, 1, now, now, now))
    fragment = SourceFragmentRepository(database).replace_for_source(
        ExtractionResult(source.id or 0, (SourceFragment(None, source.id or 0, None, 0, "receipt and warranty", "entire file"),))
    )[0]
    records = RecordService(database)
    records.create_receipt_from_proposal(
        ReceiptRecordProposal(ReceiptRecord(None, source.id or 0, "Corner Store", 1250, "SGD", None, "R-42"), {"merchant": fragment.id or 0})
    )
    records.create_warranty_from_proposal(
        WarrantyRecordProposal(WarrantyRecord(None, source.id or 0, "Laptop Pro", "Example Corp", "W-100", None), {"product_name": fragment.id or 0})
    )
    service = ReadOnlyToolService(sources, SourceFragmentRepository(database), LexicalSearchService(sources, SourceFragmentRepository(database)), KnowledgeService(database), records, WorkspaceRepository(database), ActivityService(database))

    assert json.loads(service.search_records("corner"))[0]["record_type"] == "receipt"
    assert json.loads(service.search_records("laptop"))[0]["record_type"] == "warranty"


def test_read_only_tools_do_not_return_private_source_text_to_cloud_agent(tmp_path: Path) -> None:
    database = tmp_path / "steward.db"
    initialize_database(database)
    now = datetime(2026, 9, 8, tzinfo=UTC)
    sources = SourceRepository(database)
    source = sources.add(Source(None, tmp_path / "private.md", "b" * 64, SourceType.MARKDOWN, 1, now, now, now))
    fragments = SourceFragmentRepository(database)
    fragments.replace_for_source(
        ExtractionResult(source.id or 0, (SourceFragment(None, source.id or 0, None, 0, "Do not disclose this.", "lines 1"),))
    )
    privacy = PrivacyService(database)
    privacy.set_rule(source.id or 0, PrivacyRule.NO_MODEL)
    service = ReadOnlyToolService(
        sources, fragments, LexicalSearchService(sources, fragments), KnowledgeService(database),
        RecordService(database), WorkspaceRepository(database), ActivityService(database), privacy,
    )

    assert json.loads(service.search_sources("disclose")) == []
    assert "privacy rule" in json.loads(service.read_source(source.id or 0))["error"]


def test_read_only_tools_recover_when_an_agent_searches_a_markdown_filename(tmp_path: Path) -> None:
    database = tmp_path / "steward.db"
    initialize_database(database)
    now = datetime(2026, 9, 8, tzinfo=UTC)
    sources = SourceRepository(database)
    source = sources.add(Source(None, tmp_path / "COURSE_DETAILS.md", "a" * 64, SourceType.MARKDOWN, 1, now, now, now))
    fragments = SourceFragmentRepository(database)
    fragments.replace_for_source(
        ExtractionResult(source.id or 0, (SourceFragment(None, source.id or 0, None, 0, "Course details for CS3210.", "lines 1"),))
    )
    service = ReadOnlyToolService(
        sources, fragments, LexicalSearchService(sources, fragments), KnowledgeService(database),
        RecordService(database), WorkspaceRepository(database), ActivityService(database),
    )

    results = json.loads(service.search_sources("COURSE_DETAILS.md"))

    assert results[0]["source_id"] == source.id
