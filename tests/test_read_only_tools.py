import json
from datetime import UTC, datetime
from pathlib import Path
from dataclasses import replace

from steward.activity import ActivityService, ActivityType
from steward.action_proposals import ActionProposalRepository
from steward.extraction import ExtractionResult, SourceFragment, SourceFragmentRepository
from steward.knowledge import ConflictResolution, KnowledgeService, KnowledgeEnrichmentProposalRepository
from steward.privacy import PrivacyRule, PrivacyService
from steward.records import (
    HotelReservationRecord,
    HotelReservationRecordProposal,
    ReceiptRecord,
    ReceiptRecordProposal,
    RecordService,
    WarrantyRecord,
    WarrantyRecordProposal,
)
from steward.retrieval import LexicalSearchService
from steward.sources import Source, SourceRepository, SourceType, SourceStatus
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
    activity.record(
        ActivityType.SOURCE_CAPTURED,
        object_id=str(source.id),
        details=str(tmp_path / "private" / "tlb.md"),
    )
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
    assert source_hits[0]["filename"] == "tlb.md"
    assert "path" not in source_hits[0]
    assert read["filename"] == "tlb.md"
    assert "path" not in read
    assert read["fragments"][0]["text"] == "TLBs cache translations."
    assert found_concept["concept"]["id"] == concept.id
    assert found_workspaces[0]["name"] == "Operating Systems"
    assert events[0]["event_type"] == "source_captured"
    assert events[0]["details"] == "local file: tlb.md"
    assert str(tmp_path) not in json.dumps({"hits": source_hits, "read": read, "events": events})
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


def test_knowledge_tool_respects_evidence_privacy_and_surfaces_reviewed_conflicts(tmp_path: Path) -> None:
    database = tmp_path / "steward.db"
    initialize_database(database)
    now = datetime(2026, 9, 8, tzinfo=UTC)
    sources = SourceRepository(database)
    source = sources.add(Source(None, tmp_path / "tlb.md", "a" * 64, SourceType.MARKDOWN, 0, now, now, now))
    fragments = SourceFragmentRepository(database)
    fragment = fragments.replace_for_source(ExtractionResult(source.id, (
        SourceFragment(None, source.id, None, 0, "TLBs do not cache translations.", "line 1"),
    )))[0]
    knowledge = KnowledgeService(database)
    concept = knowledge.create_concept("TLB")
    claim = knowledge.create_claim(concept.id, "TLBs cache translations.", [fragment.id])
    proposals = KnowledgeEnrichmentProposalRepository(database)
    review = proposals.add(knowledge.compare_evidence(claim, fragment_id=fragment.id, evidence_text=fragment.text))
    privacy = PrivacyService(database)
    service = ReadOnlyToolService(sources, fragments, LexicalSearchService(sources, fragments), knowledge, RecordService(database), WorkspaceRepository(database), ActivityService(database), privacy)
    assert json.loads(service.search_knowledge("TLB"))["concept"]["claims"][0]["accepted_reviews"] == []
    proposals.review(review.id, "accepted")
    result = json.loads(service.search_knowledge("TLB"))["concept"]["claims"][0]
    assert result["accepted_reviews"][0]["operation"] == "contradict"
    assert result["accepted_reviews"][0]["text"] == fragment.text
    assert result["accepted_reviews"][0]["conflict_resolution"] is None
    assert "not proven truth" in result["review_caveat"]
    proposals.resolve_conflict(review.id, ConflictResolution.NEEDS_REVISION)
    actions = ActionProposalRepository(database)
    revision = actions.add("revise_knowledge_claim", {
        "conflict_proposal_id": str(review.id), "claim_id": str(claim.id),
        "fragment_id": str(fragment.id), "replacement_text": "TLBs may cache translations.",
    })
    replacement = knowledge.accept_claim_revision(revision.id or 0)
    revised = json.loads(service.search_knowledge("TLB"))["concept"]["claims"]
    by_id = {item["id"]: item for item in revised}
    assert by_id[claim.id]["lineage"] == {
        "status": "superseded", "replaced_by_claim_id": replacement.id, "revises_claim_id": None,
    }
    assert by_id[replacement.id]["lineage"] == {
        "status": "reviewed_revision", "replaced_by_claim_id": None, "revises_claim_id": claim.id,
    }
    assert by_id[claim.id]["accepted_reviews"][0]["conflict_resolution"] == "needs_revision"
    sources.update(replace(source, status=SourceStatus.MISSING))
    assert json.loads(service.search_knowledge("TLB"))["concept"] is None
    assert "error" in json.loads(service.read_source(source.id))
    assert service.search_sources("translations") == "[]"
    sources.update(source)
    privacy.set_rule(source.id, PrivacyRule.LOCAL_MODEL_ONLY)
    assert json.loads(service.search_knowledge("TLB"))["concept"] is None
    local = ReadOnlyToolService(sources, fragments, LexicalSearchService(sources, fragments), knowledge, RecordService(database), WorkspaceRepository(database), ActivityService(database), privacy, model_is_local=True)
    local_claims = json.loads(local.search_knowledge("TLB"))["concept"]["claims"]
    assert len(local_claims) == 2
    assert {item["lineage"]["status"] for item in local_claims} == {
        "superseded", "reviewed_revision",
    }
    privacy.set_rule(source.id, PrivacyRule.NO_MODEL)
    assert json.loads(local.search_knowledge("TLB"))["concept"] is None


def test_read_only_record_search_includes_receipts_warranties_and_hotels(tmp_path: Path) -> None:
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
    records.create_hotel_reservation_from_proposal(
        HotelReservationRecordProposal(
            HotelReservationRecord(None, source.id or 0, "Marina Bay Hotel", "H-42", None, None, "Ada Lovelace"),
            {"property_name": fragment.id or 0},
        )
    )
    service = ReadOnlyToolService(sources, SourceFragmentRepository(database), LexicalSearchService(sources, SourceFragmentRepository(database)), KnowledgeService(database), records, WorkspaceRepository(database), ActivityService(database))

    assert json.loads(service.search_records("corner"))[0]["record_type"] == "receipt"
    assert json.loads(service.search_records("laptop"))[0]["record_type"] == "warranty"
    assert json.loads(service.search_records("marina"))[0]["record_type"] == "hotel"


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


def test_read_only_tools_allow_local_models_to_read_local_only_sources(tmp_path: Path) -> None:
    database = tmp_path / "steward.db"; initialize_database(database)
    now = datetime(2026, 9, 8, tzinfo=UTC)
    sources = SourceRepository(database)
    source = sources.add(Source(None, tmp_path / "local.md", "c" * 64, SourceType.MARKDOWN, 1, now, now, now))
    fragments = SourceFragmentRepository(database)
    fragments.replace_for_source(
        ExtractionResult(source.id or 0, (SourceFragment(None, source.id or 0, None, 0, "Local-only OpenMP notes.", "lines 1"),))
    )
    privacy = PrivacyService(database)
    privacy.set_rule(source.id or 0, PrivacyRule.LOCAL_MODEL_ONLY)

    cloud = ReadOnlyToolService(
        sources, fragments, LexicalSearchService(sources, fragments), KnowledgeService(database),
        RecordService(database), WorkspaceRepository(database), ActivityService(database), privacy,
    )
    local = ReadOnlyToolService(
        sources, fragments, LexicalSearchService(sources, fragments), KnowledgeService(database),
        RecordService(database), WorkspaceRepository(database), ActivityService(database), privacy,
        model_is_local=True,
    )

    assert json.loads(cloud.search_sources("OpenMP")) == []
    assert json.loads(local.search_sources("OpenMP"))[0]["source_id"] == source.id
    assert "Local-only OpenMP notes." in json.loads(local.read_source(source.id or 0))["fragments"][0]["text"]


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


def test_read_only_tool_limits_bound_invalid_model_arguments(tmp_path: Path) -> None:
    """Model-generated result limits cannot terminate an otherwise safe search."""
    database = tmp_path / "steward.db"
    initialize_database(database)
    service = ReadOnlyToolService(
        SourceRepository(database),
        SourceFragmentRepository(database),
        LexicalSearchService(SourceRepository(database), SourceFragmentRepository(database)),
        KnowledgeService(database),
        RecordService(database),
        WorkspaceRepository(database),
        ActivityService(database),
    )

    assert service._limit(0) == 1
    assert service._limit(21) == 20
