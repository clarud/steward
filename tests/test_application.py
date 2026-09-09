from datetime import UTC, datetime, timedelta
from pathlib import Path
import sqlite3

from steward.answer import AnswerCitation
from steward.application import (
    StewardActionProposalApplication,
    StewardCaptureApplication,
    StewardDriveImportApplication,
    StewardGmailImportApplication,
    StewardEventApplication,
    StewardOrganizationApprovalApplication,
    StewardQuestionApplication,
    StewardReadApplication,
    StewardProvisionalIntakeApplication,
    StewardToolAgentApplication,
    StewardRecordApplication,
    StewardKnowledgeApplication,
    StewardRootsApplication,
    StewardPrivacyApplication,
    StewardOperationsApplication,
    StewardCalendarApplication,
    StewardTaskApplication,
    StewardResearchApplication,
    StewardCuratedNoteApplication,
    StewardWorkspaceLinkApplication,
    StewardIntegrationStatusApplication,
    TEXT_QUESTION_REQUIRED,
)
from steward.action_proposals import ActionProposalRepository, ActionProposalService
from steward.calendar import CalendarEventProposalService, CalendarService
from steward.records import ReceiptRecord, ReceiptRecordProposal, RecordService, TravelRecord
from steward.capture import CaptureResult, InboxCaptureService
from steward.events import IncomingEvent
from steward.organization import (
    OrganizationApprovalService,
    OrganizationApprovalThreadRepository,
    OrganizationProposal,
    OrganizationProposalRepository,
)
from steward.actions import FileMutationService
from steward.activity import ActivityService, ActivityType
from steward.extraction import ExtractionResult, MarkdownExtractor, SourceFragment, SourceFragmentRepository
from steward.graphs import build_organization_approval_graph
from steward.sources import Source, SourceRepository, SourceType
from steward.sources.service import SourceService
from steward.storage import initialize_database
from steward.workspaces import WorkspaceRepository, WorkspaceService
from steward.retrieval import HybridSearchHit, LexicalSearchService, SemanticSearchHit
from steward.presentation import PresentedReply
from steward.intake import ProvisionalIntakeRepository, ProvisionalIntakeService
from steward.knowledge import KnowledgeEnrichmentProposalRepository, KnowledgeService
from steward.knowledge_connector import KnowledgeConnector
from steward.roots import SourceRootRepository
from steward.privacy import PrivacyRule, PrivacyService
from steward.telegram import TelegramUpdateDeliveryRepository
from steward.tasks import TaskReminderService, TaskService
from steward.research import ResearchBundle, ResearchRetentionService, ResearchSource
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.errors import GraphRecursionError


class FakeGraph:
    def __init__(self) -> None:
        self.inputs: list[dict[str, str]] = []

    def invoke(self, input: dict[str, str], config=None) -> dict[str, str]:
        self.inputs.append(input)
        return {"answer": "A TLB caches address translations. [F1]"}


def make_event(*, text: str | None, reply_text: str | None = None) -> IncomingEvent:
    return IncomingEvent(
        id="telegram:42",
        platform="telegram",
        chat_id="100",
        message_id="7",
        reply_to_id=None,
        timestamp=datetime(2026, 9, 7, tzinfo=UTC),
        text=text,
        reply_text=reply_text,
    )


def test_question_application_passes_normalized_text_to_graph() -> None:
    graph = FakeGraph()

    response = StewardQuestionApplication(graph).handle(make_event(text="What is a TLB?"))

    assert graph.inputs == [{"question": "What is a TLB?"}]
    assert response == "A TLB caches address translations. [F1]"


def test_question_application_does_not_invoke_graph_for_empty_text() -> None:
    graph = FakeGraph()

    response = StewardQuestionApplication(graph).handle(make_event(text="   "))

    assert response == TEXT_QUESTION_REQUIRED
    assert graph.inputs == []


def test_question_application_includes_source_details_for_citations() -> None:
    class CitedGraph:
        def invoke(self, input: dict[str, str], config=None):
            return {
                "answer": "A TLB caches translations. [F1]",
                "citations": (
                    AnswerCitation(
                        key="F1",
                        fragment_id=3,
                        source_path=Path("vault/virtual-memory.md"),
                        heading="TLB",
                        location="lines 4-6",
                    ),
                ),
            }

    response = StewardQuestionApplication(CitedGraph()).handle(
        make_event(text="What is a TLB?")
    )

    assert response == (
        "A TLB caches translations. [F1]\n\n"
        "Sources:\n[F1] virtual-memory.md:lines 4-6 [TLB]"
    )


def test_capture_application_requires_explicit_text() -> None:
    class FakeCaptureService:
        def capture_text(self, event):
            raise AssertionError("empty capture must not be persisted")

    assert StewardCaptureApplication(FakeCaptureService()).handle(make_event(text="/save")) == (
        "Use /save followed by the text you want Steward to keep."
    )


def test_event_application_routes_a_question() -> None:
    graph = FakeGraph()
    class Capture:
        def handle(self, event): raise AssertionError("question must not capture")
    response = StewardEventApplication(StewardQuestionApplication(graph), Capture()).handle(
        make_event(text="What is a TLB?")
    )
    assert "TLB" in response


def test_help_explains_read_boundaries_and_reviewable_writes() -> None:
    help_text = StewardReadApplication.help_text()

    assert "Read-only:" in help_text
    assert "/propose_task TEXT" in help_text
    assert "/research QUESTION" in help_text
    assert "/approve_action ID" in help_text
    assert "review-required writes" in help_text.casefold()


def test_event_application_routes_owner_safe_reads_and_workspace_proposals(tmp_path: Path) -> None:
    database_path = tmp_path / "steward.db"
    initialize_database(database_path)
    inbox = tmp_path / "vault" / "inbox"
    inbox.mkdir(parents=True)
    source_path = inbox / "openmp.md"
    source_path.write_text("OpenMP notes", encoding="utf-8")
    now = datetime(2026, 9, 8, tzinfo=UTC)
    sources = SourceRepository(database_path)
    source = sources.add(
        Source(None, source_path, "a" * 64, SourceType.MARKDOWN, 11, now, now, now)
    )
    fragments = SourceFragmentRepository(database_path)
    fragments.replace_for_source(
        ExtractionResult(
            source.id or 0,
            (SourceFragment(None, source.id or 0, "OpenMP", 0, "OpenMP scheduling notes", "lines 1-1"),),
        )
    )
    activity = ActivityService(database_path)
    activity.record(ActivityType.SOURCE_CAPTURED, object_id=str(source.id), details="Inbox capture")
    workspaces = WorkspaceRepository(database_path)
    reads = StewardReadApplication(
        sources,
        fragments,
        LexicalSearchService(sources, fragments),
        workspaces,
        activity,
        inbox,
    )
    actions = StewardActionProposalApplication(
        ActionProposalRepository(database_path),
        ActionProposalService(ActionProposalRepository(database_path), workspaces, activity),
    )
    application = StewardEventApplication(
        StewardQuestionApplication(FakeGraph()),
        StewardCaptureApplication(type("Capture", (), {})()),
        action_proposal_application=actions,
        read_application=reads,
    )

    assert "openmp.md" in application.handle(make_event(text="/inbox"))
    assert "Search results" in application.handle(make_event(text="/search OpenMP"))
    assert "Recent activity" in application.handle(make_event(text="/activity"))
    source_details = application.handle(make_event(text="/source 1"))
    assert "Filename: openmp.md" in source_details
    assert str(tmp_path) not in source_details
    assert "openmp.md" in application.handle(make_event(text="what is in my inbox"))
    assert "Recent activity" in application.handle(make_event(text="show my recent activity"))
    assert "Search results" in application.handle(make_event(text="find my notes on OpenMP"))
    assert "Inbox" in application.handle(
        make_event(text="organize my inbox")
    )
    assert "Workspace proposal 1" in application.handle(
        make_event(text="create a workspace for jobs")
    )


def test_telegram_exposes_local_semantic_and_hybrid_search_without_a_model_call(tmp_path: Path) -> None:
    database_path = tmp_path / "steward.db"
    initialize_database(database_path)
    inbox = tmp_path / "vault" / "inbox"; inbox.mkdir(parents=True)
    source_path = inbox / "memory.md"; source_path.write_text("TLB notes", encoding="utf-8")
    now = datetime(2026, 9, 8, tzinfo=UTC)
    sources = SourceRepository(database_path)
    source = sources.add(Source(None, source_path, "a" * 64, SourceType.MARKDOWN, 9, now, now, now))
    fragments = SourceFragmentRepository(database_path)
    fragment = fragments.replace_for_source(ExtractionResult(
        source.id or 0,
        (SourceFragment(None, source.id or 0, "TLB", 0, "A TLB caches translations.", "lines 1-1"),),
    ))[0]

    class Semantic:
        def search(self, query: str, *, limit: int):
            assert query == "CPU translation cache" and limit == 5
            return (SemanticSearchHit(source, fragment, 0.91),)

    class Hybrid:
        def search(self, query: str, *, limit: int):
            assert query == "CPU translation cache" and limit == 5
            return (HybridSearchHit(source, fragment, 0.031, None, 0.91),)

    reads = StewardReadApplication(
        sources, fragments, LexicalSearchService(sources, fragments),
        WorkspaceRepository(database_path), ActivityService(database_path), inbox,
        semantic_search=Semantic(), hybrid_retriever=Hybrid(),
    )

    semantic = reads.handle_command(make_event(text="/semantic_search CPU translation cache"))
    hybrid = reads.handle_command(make_event(text="/hybrid_search CPU translation cache"))

    assert "Semantic search results" in semantic and "memory.md" in semantic
    assert "similarity 0.91" in semantic and str(tmp_path) not in semantic
    assert "Hybrid search results" in hybrid and "fusion 0.031" in hybrid


def test_telegram_semantic_search_has_a_safe_unavailable_response(tmp_path: Path) -> None:
    database_path = tmp_path / "steward.db"; initialize_database(database_path)

    class UnavailableSemantic:
        def search(self, query: str, *, limit: int):
            raise RuntimeError("C:/private/embedding-cache is missing")

    reads = StewardReadApplication(
        SourceRepository(database_path), SourceFragmentRepository(database_path),
        LexicalSearchService(SourceRepository(database_path), SourceFragmentRepository(database_path)),
        WorkspaceRepository(database_path), ActivityService(database_path), tmp_path,
        semantic_search=UnavailableSemantic(),
    )

    response = reads.handle_command(make_event(text="/semantic_search cache"))

    assert response == "Semantic search is temporarily unavailable. Verify the local embedding model and derived index."
    assert "C:/private" not in response


def test_activity_hides_local_directory_structure_from_telegram(tmp_path: Path) -> None:
    database_path = tmp_path / "steward.db"
    initialize_database(database_path)
    inbox = tmp_path / "private" / "vault" / "inbox"
    inbox.mkdir(parents=True)
    activity = ActivityService(database_path)
    activity.record(
        ActivityType.SOURCE_CAPTURED,
        object_id="1",
        details=str(inbox / "private-note.md"),
    )
    reads = StewardReadApplication(
        SourceRepository(database_path),
        SourceFragmentRepository(database_path),
        LexicalSearchService(SourceRepository(database_path), SourceFragmentRepository(database_path)),
        WorkspaceRepository(database_path),
        activity,
        inbox,
    )

    response = reads.activity("")

    assert "private-note.md" in response
    assert str(tmp_path) not in response
    assert "local file:" in response


def test_status_reports_review_and_delivery_health_without_message_content(tmp_path: Path) -> None:
    database = tmp_path / "steward.db"; initialize_database(database)
    inbox = tmp_path / "vault" / "inbox"; inbox.mkdir(parents=True)
    actions = ActionProposalRepository(database)
    actions.add("create_workspace", {"name": "School"})
    deliveries = TelegramUpdateDeliveryRepository(database)
    assert deliveries.claim("telegram:health")
    reads = StewardReadApplication(
        SourceRepository(database), SourceFragmentRepository(database),
        LexicalSearchService(SourceRepository(database), SourceFragmentRepository(database)),
        WorkspaceRepository(database), ActivityService(database), inbox, actions, deliveries,
    )

    response = reads.status()

    assert "Pending action reviews: 1" in response
    assert "Telegram delivery: 1 processing, 0 dead letters" in response


def test_event_application_gives_helpful_unknown_response() -> None:
    response = StewardEventApplication(
        StewardQuestionApplication(FakeGraph()), StewardCaptureApplication(type("Capture", (), {})())
    ).handle(make_event(text="please do a mysterious thing"))

    assert "Try /help" in response


def test_agent_command_uses_a_persistent_chat_scoped_tool_thread() -> None:
    class ToolGraph:
        def __init__(self) -> None:
            self.input = None
            self.config = None

        def invoke(self, input, config):
            self.input = input
            self.config = config
            return {"messages": [type("Final", (), {"content": "Found local evidence."})()]}

    graph = ToolGraph()
    application = StewardEventApplication(
        StewardQuestionApplication(FakeGraph()),
        StewardCaptureApplication(type("Capture", (), {})()),
        tool_agent_application=StewardToolAgentApplication(graph),
    )

    response = application.handle(make_event(text="/agent what do I know about OpenMP"))

    assert response == "Found local evidence."
    assert graph.config["configurable"]["thread_id"] == "tool-agent:telegram:100"
    assert graph.input["messages"][1].content == "what do I know about OpenMP"


def test_agent_command_turns_a_graph_recursion_limit_into_a_safe_reply() -> None:
    class LoopingToolGraph:
        def invoke(self, input, config):
            raise GraphRecursionError("tool loop did not terminate")

    application = StewardEventApplication(
        StewardQuestionApplication(FakeGraph()),
        StewardCaptureApplication(type("Capture", (), {})()),
        tool_agent_application=StewardToolAgentApplication(LoopingToolGraph()),
    )

    response = application.handle(make_event(text="/agent find all CS3210 notes"))

    assert response == (
        "I stopped the tool workflow before it could loop further. "
        "No write was performed; please narrow the request and try again."
    )


def test_travel_record_preview_is_evidence_backed_and_does_not_persist(tmp_path: Path) -> None:
    database_path = tmp_path / "steward.db"
    initialize_database(database_path)
    now = datetime(2026, 9, 9, tzinfo=UTC)
    source_path = tmp_path / "itinerary.md"
    source_path.write_text("itinerary", encoding="utf-8")
    sources = SourceRepository(database_path)
    source = sources.add(Source(None, source_path, "a" * 64, SourceType.MARKDOWN, 9, now, now, now))
    fragments = SourceFragmentRepository(database_path)
    fragments.replace_for_source(
        ExtractionResult(
            source.id or 0,
            (SourceFragment(None, source.id or 0, None, 0,
                "Flight SQ638\nDeparture: Singapore\nArrival: Tokyo\nBooking Reference: ABC123", "lines 1-4"),),
        )
    )
    records = RecordService(database_path)
    activity = ActivityService(database_path)
    action_repository = ActionProposalRepository(database_path)
    action_application = StewardActionProposalApplication(
        action_repository,
        ActionProposalService(action_repository, WorkspaceRepository(database_path), activity),
        record_service=records,
        fragment_repository=fragments,
        activity_service=activity,
    )
    application = StewardEventApplication(
        StewardQuestionApplication(FakeGraph()), StewardCaptureApplication(type("Capture", (), {})()),
        action_proposal_application=action_application,
        record_application=StewardRecordApplication(records, fragments, action_repository, activity),
    )

    response = application.handle(make_event(text="/propose_travel_record 1"))

    assert isinstance(response, PresentedReply)
    assert "flight: SQ638" in response.text
    assert "fragment 1" in response.text
    assert records.list_travel_records() == []

    accepted = application.handle(make_event(text="/approve_action 1"))

    assert accepted == "Travel record 1 created from source 1."
    assert records.list_travel_records()[0].flight_number == "SQ638"


def test_telegram_travel_correction_is_reviewed_before_mutation(tmp_path: Path) -> None:
    database = tmp_path / "steward.db"; initialize_database(database)
    now = datetime(2026, 9, 10, tzinfo=UTC)
    source_path = tmp_path / "trip.md"; source_path.write_text("trip", encoding="utf-8")
    source = SourceRepository(database).add(Source(None, source_path, "a" * 64, SourceType.MARKDOWN, 4, now, now, now))
    records = RecordService(database)
    record = records.create_travel_record(TravelRecord(None, source.id or 0, "SQ638", "Singapore", "Tokyo", None, None, None))
    activity = ActivityService(database); proposals = ActionProposalRepository(database)
    application = StewardEventApplication(
        StewardQuestionApplication(FakeGraph()), StewardCaptureApplication(type("Capture", (), {})()),
        record_application=StewardRecordApplication(records, SourceFragmentRepository(database), proposals, activity),
        action_proposal_application=StewardActionProposalApplication(
            proposals, ActionProposalService(proposals, WorkspaceRepository(database), activity),
            record_service=records, activity_service=activity,
        ),
    )

    preview = application.handle(make_event(text=f"/correct_travel_record {record.id} arrival Osaka"))

    assert isinstance(preview, PresentedReply)
    assert records.list_travel_records()[0].arrival == "Tokyo"
    assert application.handle(make_event(text="/approve_action 1")) == "Travel record 1 corrected: arrival."
    assert records.list_travel_records()[0].arrival == "Osaka"


def test_telegram_receipt_correction_is_reviewed_before_mutation(tmp_path: Path) -> None:
    database = tmp_path / "steward.db"; initialize_database(database)
    now = datetime(2026, 9, 10, tzinfo=UTC)
    source_path = tmp_path / "receipt.md"; source_path.write_text("receipt", encoding="utf-8")
    source = SourceRepository(database).add(Source(None, source_path, "a" * 64, SourceType.MARKDOWN, 4, now, now, now))
    fragment = SourceFragmentRepository(database).replace_for_source(
        ExtractionResult(source.id or 0, (SourceFragment(None, source.id or 0, None, 0, "receipt", "entire file"),))
    )[0]
    records = RecordService(database)
    receipt = records.create_receipt_from_proposal(
        ReceiptRecordProposal(ReceiptRecord(None, source.id or 0, "Campus Cafe", 500, "SGD", None, None), {"merchant": fragment.id or 0})
    )
    activity = ActivityService(database); proposals = ActionProposalRepository(database)
    application = StewardEventApplication(
        StewardQuestionApplication(FakeGraph()), StewardCaptureApplication(type("Capture", (), {})()),
        record_application=StewardRecordApplication(records, SourceFragmentRepository(database), proposals, activity),
        action_proposal_application=StewardActionProposalApplication(
            proposals, ActionProposalService(proposals, WorkspaceRepository(database), activity),
            record_service=records, activity_service=activity,
        ),
    )

    preview = application.handle(make_event(text=f"/correct_receipt_record {receipt.id} total_cents 12.50"))

    assert isinstance(preview, PresentedReply)
    assert records.list_receipt_records()[0].total_cents == 500
    assert application.handle(make_event(text="/approve_action 1")) == "Receipt record 1 corrected: total_cents."
    assert records.list_receipt_records()[0].total_cents == 1250


def test_knowledge_enrichment_is_reviewed_with_claim_and_fragment_ids(tmp_path: Path) -> None:
    database_path = tmp_path / "steward.db"; initialize_database(database_path)
    now = datetime(2026, 9, 9, tzinfo=UTC)
    source_path = tmp_path / "note.md"; source_path.write_text("note", encoding="utf-8")
    source = SourceRepository(database_path).add(
        Source(None, source_path, "a" * 64, SourceType.MARKDOWN, 4, now, now, now)
    )
    fragments = SourceFragmentRepository(database_path)
    fragments.replace_for_source(
        ExtractionResult(source.id or 0, (SourceFragment(None, source.id or 0, None, 0,
            "A TLB caches recently used address translations.", "lines 1-1"),))
    )
    knowledge = KnowledgeService(database_path)
    concept = knowledge.create_concept("TLB")
    claim = knowledge.create_claim(concept.id or 0, "A TLB caches address translations.", [1])
    activity = ActivityService(database_path)
    application = StewardEventApplication(
        StewardQuestionApplication(FakeGraph()), StewardCaptureApplication(type("Capture", (), {})()),
        knowledge_application=StewardKnowledgeApplication(
            knowledge, fragments, KnowledgeEnrichmentProposalRepository(database_path), activity
        ),
    )

    preview = application.handle(make_event(text="/propose_enrichment 1 1"))

    assert isinstance(preview, PresentedReply)
    assert "CONFIRM claim 1" in preview.text
    assert preview.actions[0].command == "/review_enrichment 1 accepted"
    accepted = application.handle(make_event(text="/review_enrichment 1 accepted"))
    assert accepted == "Knowledge enrichment proposal 1 accepted."


def test_telegram_conflict_review_shows_claim_evidence_and_preserves_the_claim(tmp_path: Path) -> None:
    database = tmp_path / "steward.db"; initialize_database(database)
    now = datetime(2026, 9, 10, tzinfo=UTC)
    source = SourceRepository(database).add(
        Source(None, tmp_path / "contradiction.md", "a" * 64, SourceType.MARKDOWN, 0, now, now, now)
    )
    fragments = SourceFragmentRepository(database)
    fragment = fragments.replace_for_source(ExtractionResult(source.id or 0, (
        SourceFragment(None, source.id or 0, "TLB", 0, "TLBs do not cache translations.", "lines 4-4"),
    )))[0]
    knowledge = KnowledgeService(database)
    concept = knowledge.create_concept("TLB")
    claim = knowledge.create_claim(concept.id or 0, "TLBs cache translations.", [fragment.id or 0])
    application = StewardEventApplication(
        StewardQuestionApplication(FakeGraph()), StewardCaptureApplication(type("Capture", (), {})()),
        knowledge_application=StewardKnowledgeApplication(
            knowledge, fragments, KnowledgeEnrichmentProposalRepository(database), ActivityService(database),
            source_repository=SourceRepository(database),
        ),
    )

    preview = application.handle(make_event(text=f"/propose_enrichment {claim.id} {fragment.id}"))

    assert isinstance(preview, PresentedReply)
    assert "CONTRADICT" in preview.text
    assert "Existing claim 1: TLBs cache translations." in preview.text
    assert "contradiction.md" in preview.text and "TLBs do not cache translations." in preview.text
    assert "does not rewrite the existing claim" in preview.text
    assert application.handle(make_event(text="/review_enrichment 1 accepted")) == (
        "Knowledge contradiction proposal 1 accepted. Existing claim unchanged."
    )
    assert knowledge.get_claim(claim.id or 0) == claim


def test_telegram_lists_evidence_backed_knowledge_connections_without_mutating(tmp_path: Path) -> None:
    database = tmp_path / "steward.db"; initialize_database(database)
    now = datetime(2026, 9, 10, tzinfo=UTC)
    source = SourceRepository(database).add(
        Source(None, tmp_path / "note.md", "a" * 64, SourceType.MARKDOWN, 0, now, now, now)
    )
    fragments = SourceFragmentRepository(database)
    fragment = fragments.replace_for_source(ExtractionResult(source.id or 0, (
        SourceFragment(None, source.id or 0, None, 0, "TLBs and page tables work together.", "lines 1-1"),
    )))[0]
    knowledge = KnowledgeService(database)
    tlb = knowledge.create_concept("TLB")
    page_tables = knowledge.create_concept("Page Tables")
    knowledge.create_claim(tlb.id or 0, "TLBs cache translations.", [fragment.id or 0])
    knowledge.create_claim(page_tables.id or 0, "Page tables map addresses.", [fragment.id or 0])
    application = StewardEventApplication(
        StewardQuestionApplication(FakeGraph()), StewardCaptureApplication(type("Capture", (), {})()),
        knowledge_application=StewardKnowledgeApplication(
            knowledge, fragments, KnowledgeEnrichmentProposalRepository(database), ActivityService(database),
            KnowledgeConnector(database),
        ),
    )

    response = application.handle(make_event(text="/connect_knowledge"))

    assert "TLB ↔ Page Tables" in response
    assert "Evidence fragments: 1" in response
    assert "not that the concepts are interchangeable" in response
    assert KnowledgeConnector(database).propose()[0].supporting_fragment_ids == (fragment.id,)


def test_roots_command_reports_only_locally_authorized_root_health(tmp_path: Path) -> None:
    database_path = tmp_path / "steward.db"; initialize_database(database_path)
    root_path = tmp_path / "notes"; root_path.mkdir()
    roots = SourceRootRepository(database_path)
    roots.add("School", root_path)
    application = StewardEventApplication(
        StewardQuestionApplication(FakeGraph()), StewardCaptureApplication(type("Capture", (), {})()),
        roots_application=StewardRootsApplication(roots),
    )

    response = application.handle(make_event(text="/roots"))

    assert response == "Authorized source roots:\n1: School — available"
    roots.set_enabled("School", False)
    assert application.handle(make_event(text="/roots")) == "Authorized source roots:\n1: School — disabled"


def test_privacy_commands_change_only_one_known_source_policy(tmp_path: Path) -> None:
    database_path = tmp_path / "steward.db"; initialize_database(database_path)
    now = datetime(2026, 9, 9, tzinfo=UTC)
    path = tmp_path / "note.md"; path.write_text("note", encoding="utf-8")
    sources = SourceRepository(database_path)
    sources.add(Source(None, path, "a" * 64, SourceType.MARKDOWN, 4, now, now, now))
    activity = ActivityService(database_path)
    application = StewardEventApplication(
        StewardQuestionApplication(FakeGraph()), StewardCaptureApplication(type("Capture", (), {})()),
        privacy_application=StewardPrivacyApplication(PrivacyService(database_path), sources, activity),
    )

    changed = application.handle(make_event(text="/set_privacy 1 local_model_only"))
    inspected = application.handle(make_event(text="/privacy 1"))

    assert changed == "Source 1 privacy rule set to local_model_only."
    assert inspected == "Source 1 privacy rule: local_model_only"
    assert activity.list_recent()[0].event_type == ActivityType.SOURCE_PRIVACY_CHANGED
    assert activity.list_recent()[0].object_id == "1"


def test_delivery_diagnostics_expose_metadata_but_never_message_content(tmp_path: Path) -> None:
    database_path = tmp_path / "steward.db"; initialize_database(database_path)
    deliveries = TelegramUpdateDeliveryRepository(database_path)
    assert deliveries.claim("telegram:123")
    deliveries.mark_delivered("telegram:123")
    application = StewardEventApplication(
        StewardQuestionApplication(FakeGraph()), StewardCaptureApplication(type("Capture", (), {})()),
        operations_application=StewardOperationsApplication(deliveries),
    )

    recent = application.handle(make_event(text="/deliveries"))
    history = application.handle(make_event(text="/delivery_history"))
    dead_letters = application.handle(make_event(text="/dead_letters"))

    assert "telegram:123: delivered" in recent
    assert "telegram:123: delivered" in history
    assert dead_letters == "No terminal Telegram delivery failures."


def test_telegram_delivery_recovery_requires_a_durable_approval_and_never_replays(tmp_path: Path) -> None:
    database_path = tmp_path / "steward.db"; initialize_database(database_path)
    activity = ActivityService(database_path)
    proposals = ActionProposalRepository(database_path)
    deliveries = TelegramUpdateDeliveryRepository(database_path, max_attempts=1)
    now = datetime(2026, 9, 9, tzinfo=UTC)
    assert deliveries.claim("telegram:recover", now=now)
    deliveries.release("telegram:recover", now=now)
    assert deliveries.claim("telegram:recover", now=now + timedelta(seconds=15)) is False
    application = StewardEventApplication(
        StewardQuestionApplication(FakeGraph()), StewardCaptureApplication(type("Capture", (), {})()),
        operations_application=StewardOperationsApplication(deliveries, proposals, activity),
        action_proposal_application=StewardActionProposalApplication(
            proposals, ActionProposalService(proposals, WorkspaceRepository(database_path), activity),
            activity_service=activity, delivery_repository=deliveries,
        ),
    )

    preview = application.handle(make_event(text="/recover_dead_letter telegram:recover"))

    assert isinstance(preview, PresentedReply)
    assert "does not replay a message" in preview.text
    assert deliveries.get_dead_letter("telegram:recover") is not None
    assert application.handle(make_event(text="/approve_action 1")) == (
        "Reopened telegram:recover for a future genuine Telegram redelivery. No message was replayed."
    )
    assert deliveries.get_dead_letter("telegram:recover") is None
    assert activity.list_recent()[1].event_type == ActivityType.TELEGRAM_DELIVERY_RECOVERED


def test_calendar_reads_are_available_in_telegram_without_a_model() -> None:
    class Events:
        def list(self, **kwargs):
            assert kwargs["q"] == "Tokyo"
            return type("Request", (), {"execute": lambda self: {"items": [{
                "id": "event-1", "summary": "Flight", "start": {"date": "2026-10-01"},
                "end": {"date": "2026-10-02"},
            }]}})()

        def get(self, **kwargs):
            assert kwargs["eventId"] == "event-1"
            return type("Request", (), {"execute": lambda self: {
                "id": "event-1", "summary": "Flight", "start": {"date": "2026-10-01"},
                "end": {"date": "2026-10-02"},
            }})()

    class Client:
        def events(self):
            return Events()

    application = StewardEventApplication(
        StewardQuestionApplication(FakeGraph()), StewardCaptureApplication(type("Capture", (), {})()),
        calendar_application=StewardCalendarApplication(lambda: CalendarService(Client())),
    )

    found = application.handle(make_event(text="/calendar_search Tokyo"))
    detail = application.handle(make_event(text="/calendar_get event-1"))

    assert "event-1: 2026-10-01 → 2026-10-02 — Flight" in found
    assert detail == "event-1: 2026-10-01 → 2026-10-02 — Flight"


def test_telegram_task_proposal_requires_review_before_persisting(tmp_path: Path) -> None:
    database = tmp_path / "steward.db"; initialize_database(database)
    activity = ActivityService(database)
    proposals = ActionProposalRepository(database)
    tasks = TaskService(database)
    application = StewardEventApplication(
        StewardQuestionApplication(FakeGraph()), StewardCaptureApplication(type("Capture", (), {})()),
        task_application=StewardTaskApplication(tasks, proposals, activity),
        action_proposal_application=StewardActionProposalApplication(
            proposals, ActionProposalService(proposals, WorkspaceRepository(database), activity),
            activity_service=activity, task_service=tasks,
        ),
    )

    preview = application.handle(make_event(text="/propose_task remind me to compare OpenMP scheduling before Tuesday"))

    assert isinstance(preview, PresentedReply)
    assert "compare OpenMP scheduling" in preview.text
    assert tasks.list_open() == ()
    accepted = application.handle(make_event(text="/approve_action 1"))
    assert accepted == "Task 1 created: compare OpenMP scheduling."
    assert tasks.list_open()[0].due_hint == "before Tuesday"
    assert application.handle(make_event(text="/complete_task 1")) == "Task 1 completed: compare OpenMP scheduling."
    assert tasks.list_open() == ()


def test_deterministic_natural_task_phrase_creates_the_same_reviewable_proposal(tmp_path: Path) -> None:
    database = tmp_path / "steward.db"; initialize_database(database)
    activity = ActivityService(database); proposals = ActionProposalRepository(database); tasks = TaskService(database)
    application = StewardEventApplication(
        StewardQuestionApplication(FakeGraph()), StewardCaptureApplication(type("Capture", (), {})()),
        task_application=StewardTaskApplication(tasks, proposals, activity),
    )

    response = application.handle(make_event(text="remind me to compare OpenMP scheduling before Tuesday"))

    assert isinstance(response, PresentedReply)
    assert "Task proposal 1" in response.text
    assert tasks.list_open() == ()


def test_deadline_phrase_creates_a_reviewable_task_proposal(tmp_path: Path) -> None:
    database = tmp_path / "steward.db"; initialize_database(database)
    activity = ActivityService(database); proposals = ActionProposalRepository(database); tasks = TaskService(database)
    application = StewardEventApplication(
        StewardQuestionApplication(FakeGraph()), StewardCaptureApplication(type("Capture", (), {})()),
        task_application=StewardTaskApplication(tasks, proposals, activity),
    )

    response = application.handle(make_event(text="deadline: submit CS3210 lab due Friday"))

    assert isinstance(response, PresentedReply)
    assert "Due cue: due Friday" in response.text
    assert tasks.list_open() == ()


def test_explicit_task_deadline_is_reviewed_and_persisted_with_its_timezone(tmp_path: Path) -> None:
    database = tmp_path / "steward.db"; initialize_database(database)
    activity = ActivityService(database); proposals = ActionProposalRepository(database); tasks = TaskService(database)
    application = StewardEventApplication(
        StewardQuestionApplication(FakeGraph()), StewardCaptureApplication(type("Capture", (), {})()),
        task_application=StewardTaskApplication(tasks, proposals, activity),
        action_proposal_application=StewardActionProposalApplication(
            proposals, ActionProposalService(proposals, WorkspaceRepository(database), activity),
            activity_service=activity, task_service=tasks,
        ),
    )

    preview = application.handle(
        make_event(text="/propose_task submit CS3210 lab --due-at 2026-09-18T23:59:00+08:00")
    )

    assert isinstance(preview, PresentedReply)
    assert "Due at: 2026-09-18T15:59:00+00:00" in preview.text
    assert application.handle(make_event(text="/approve_action 1")) == "Task 1 created: submit CS3210 lab."
    assert tasks.get(1).due_at == datetime(2026, 9, 18, 15, 59, tzinfo=UTC)


def test_telegram_task_reminder_requires_review_then_is_durably_scheduled(tmp_path: Path) -> None:
    database = tmp_path / "steward.db"; initialize_database(database)
    activity = ActivityService(database); proposals = ActionProposalRepository(database); tasks = TaskService(database)
    reminders = TaskReminderService(database, tasks, activity)
    application = StewardEventApplication(
        StewardQuestionApplication(FakeGraph()), StewardCaptureApplication(type("Capture", (), {})()),
        task_application=StewardTaskApplication(tasks, proposals, activity, reminders),
        action_proposal_application=StewardActionProposalApplication(
            proposals, ActionProposalService(proposals, WorkspaceRepository(database), activity),
            activity_service=activity, task_service=tasks, task_reminder_service=reminders,
        ),
    )

    preview = application.handle(
        make_event(text="/propose_task submit CS3210 lab --remind-at 2026-09-18T09:00:00+08:00")
    )

    assert isinstance(preview, PresentedReply)
    assert "Reminder at: 2026-09-18T01:00:00+00:00" in preview.text
    assert application.handle(make_event(text="/approve_action 1")) == "Task 1 created: submit CS3210 lab."
    reminder = reminders.reminder_for_task(1)
    assert reminder is not None and reminder.chat_id == "100"
    assert "reminder 2026-09-18T01:00:00+00:00" in application.handle(make_event(text="/tasks"))


def test_telegram_receipt_preview_and_approval_preserve_fragment_evidence(tmp_path: Path) -> None:
    database = tmp_path / "steward.db"; initialize_database(database)
    now = datetime(2026, 9, 10, tzinfo=UTC)
    source_path = tmp_path / "receipt.md"; source_path.write_text("receipt", encoding="utf-8")
    source = SourceRepository(database).add(
        Source(None, source_path, "a" * 64, SourceType.MARKDOWN, 7, now, now, now)
    )
    fragments = SourceFragmentRepository(database)
    fragments.replace_for_source(ExtractionResult(source.id or 0, (
        SourceFragment(None, source.id or 0, None, 0,
            "Merchant: Campus Cafe\nTotal: SGD 12.50\nReceipt Number: R-42", "lines 1-3"),
    )))
    activity = ActivityService(database); proposals = ActionProposalRepository(database); records = RecordService(database)
    application = StewardEventApplication(
        StewardQuestionApplication(FakeGraph()), StewardCaptureApplication(type("Capture", (), {})()),
        record_application=StewardRecordApplication(records, fragments, proposals, activity),
        action_proposal_application=StewardActionProposalApplication(
            proposals, ActionProposalService(proposals, WorkspaceRepository(database), activity),
            record_service=records, fragment_repository=fragments, activity_service=activity,
        ),
    )

    preview = application.handle(make_event(text="/propose_receipt_record 1"))

    assert isinstance(preview, PresentedReply)
    assert "Campus Cafe" in preview.text and "fragment 1" in preview.text
    assert records.list_receipt_records() == []
    assert application.handle(make_event(text="/approve_action 1")) == "Receipt record 1 created from source 1."
    assert records.list_receipt_records()[0].merchant == "Campus Cafe"


def test_telegram_warranty_preview_can_be_rejected_without_persisting(tmp_path: Path) -> None:
    database = tmp_path / "steward.db"; initialize_database(database)
    now = datetime(2026, 9, 10, tzinfo=UTC)
    source_path = tmp_path / "warranty.md"; source_path.write_text("warranty", encoding="utf-8")
    source = SourceRepository(database).add(Source(None, source_path, "a" * 64, SourceType.MARKDOWN, 8, now, now, now))
    fragments = SourceFragmentRepository(database)
    fragments.replace_for_source(ExtractionResult(source.id or 0, (
        SourceFragment(None, source.id or 0, None, 0, "Product: Laptop\nProvider: Example Corp", "lines 1-2"),
    )))
    activity = ActivityService(database); proposals = ActionProposalRepository(database); records = RecordService(database)
    application = StewardEventApplication(
        StewardQuestionApplication(FakeGraph()), StewardCaptureApplication(type("Capture", (), {})()),
        record_application=StewardRecordApplication(records, fragments, proposals, activity),
        action_proposal_application=StewardActionProposalApplication(
            proposals, ActionProposalService(proposals, WorkspaceRepository(database), activity),
            record_service=records, fragment_repository=fragments, activity_service=activity,
        ),
    )

    assert isinstance(application.handle(make_event(text="/propose_warranty_record 1")), PresentedReply)
    assert application.handle(make_event(text="/reject_action 1")) == "Warranty proposal 1 rejected."
    assert records.list_warranty_records() == []


def test_telegram_research_is_ephemeral_until_the_user_explicitly_retains_it(tmp_path: Path) -> None:
    database = tmp_path / "steward.db"; initialize_database(database)
    capture = InboxCaptureService(
        tmp_path / "vault" / "inbox", SourceRepository(database), SourceFragmentRepository(database), ActivityService(database)
    )

    class Provider:
        def research(self, query: str) -> ResearchBundle:
            assert query == "What is a TLB?"
            return ResearchBundle(query, "A TLB caches translations.", (ResearchSource("Reference", "https://example.test/tlb"),), provider="fake")

    application = StewardEventApplication(
        StewardQuestionApplication(FakeGraph()), StewardCaptureApplication(type("Capture", (), {})()),
        research_application=StewardResearchApplication(lambda: Provider(), ResearchRetentionService(capture)),
    )

    preview = application.handle(make_event(text="/research What is a TLB?"))
    assert isinstance(preview, PresentedReply)
    assert "ephemeral, not saved" in preview.text
    assert SourceRepository(database).list_all() == []
    retained = application.handle(make_event(text="/research_retain What is a TLB?"))
    assert "Retained external research note" in retained
    assert len(SourceRepository(database).list_all()) == 1


def test_telegram_research_card_retains_the_exact_reviewed_bundle_once(tmp_path: Path) -> None:
    class Provider:
        def __init__(self) -> None:
            self.calls = 0

        def research(self, query: str) -> ResearchBundle:
            self.calls += 1
            return ResearchBundle(query, f"answer version {self.calls}", (ResearchSource("Example", "https://example.com"),))

    database = tmp_path / "steward.db"; initialize_database(database)
    activity = ActivityService(database)
    capture = InboxCaptureService(tmp_path / "vault" / "inbox", SourceRepository(database), SourceFragmentRepository(database), activity)
    provider = Provider()
    application = StewardEventApplication(
        StewardQuestionApplication(FakeGraph()), StewardCaptureApplication(type("Capture", (), {})()),
        research_application=StewardResearchApplication(lambda: provider, ResearchRetentionService(capture)),
    )

    preview = application.handle(make_event(text="/research What is a TLB?"))
    assert isinstance(preview, PresentedReply)
    token_command = preview.actions[0].command
    retained = application.handle(make_event(text=token_command))

    assert provider.calls == 1
    assert "Retained the reviewed external research note" in retained
    note = SourceRepository(database).list_all()[0].path.read_text(encoding="utf-8")
    assert "answer version 1" in note
    assert application.handle(make_event(text=token_command)) == (
        "That research card is no longer available. Run /research again before retaining it."
    )


def test_telegram_research_card_can_retain_one_selected_source_without_the_full_answer(tmp_path: Path) -> None:
    class Provider:
        def research(self, query: str) -> ResearchBundle:
            return ResearchBundle(
                query,
                "This complete answer is not part of the selected-source reference.",
                (ResearchSource("Kernel docs", "https://example.com/tlb", "A TLB source snippet."),),
                provider="fake",
            )

    database = tmp_path / "steward.db"; initialize_database(database)
    capture = InboxCaptureService(tmp_path / "vault" / "inbox", SourceRepository(database), SourceFragmentRepository(database))
    application = StewardEventApplication(
        StewardQuestionApplication(FakeGraph()), StewardCaptureApplication(type("Capture", (), {})()),
        research_application=StewardResearchApplication(lambda: Provider(), ResearchRetentionService(capture)),
    )

    preview = application.handle(make_event(text="/research TLB shootdowns"))

    assert isinstance(preview, PresentedReply)
    source_command = preview.actions[1].command
    assert "/research_retain_source_token " in source_command
    retained = application.handle(make_event(text=source_command))
    content = SourceRepository(database).list_all()[0].path.read_text(encoding="utf-8")
    assert "Retained selected external source" in retained
    assert "Kernel docs" in content and "https://example.com/tlb" in content
    assert "complete answer is not" not in content
    assert "Already retained selected external source" in application.handle(make_event(text=source_command))


def test_telegram_curated_note_requires_review_before_becoming_an_inbox_source(tmp_path: Path) -> None:
    database = tmp_path / "steward.db"; initialize_database(database)
    activity = ActivityService(database); proposals = ActionProposalRepository(database)
    capture = InboxCaptureService(tmp_path / "vault" / "inbox", SourceRepository(database), SourceFragmentRepository(database), activity)
    application = StewardEventApplication(
        StewardQuestionApplication(FakeGraph()), StewardCaptureApplication(type("Capture", (), {})()),
        curated_note_application=StewardCuratedNoteApplication(proposals, activity),
        action_proposal_application=StewardActionProposalApplication(
            proposals, ActionProposalService(proposals, WorkspaceRepository(database), activity),
            activity_service=activity, capture_service=capture,
        ),
    )

    preview = application.handle(make_event(text="/propose_note A TLB caches recent address translations."))

    assert isinstance(preview, PresentedReply)
    assert SourceRepository(database).list_all() == []
    saved = application.handle(make_event(text="/approve_action 1"))
    assert "Saved curated note to Inbox" in saved
    assert len(SourceRepository(database).list_all()) == 1


def test_telegram_can_stage_a_replied_to_discussion_as_a_curated_note(tmp_path: Path) -> None:
    database = tmp_path / "steward.db"; initialize_database(database)
    activity = ActivityService(database); proposals = ActionProposalRepository(database)
    capture = InboxCaptureService(tmp_path / "vault" / "inbox", SourceRepository(database), SourceFragmentRepository(database), activity)
    application = StewardEventApplication(
        StewardQuestionApplication(FakeGraph()), StewardCaptureApplication(type("Capture", (), {})()),
        curated_note_application=StewardCuratedNoteApplication(proposals, activity),
        action_proposal_application=StewardActionProposalApplication(
            proposals, ActionProposalService(proposals, WorkspaceRepository(database), activity),
            activity_service=activity, capture_service=capture,
        ),
    )

    preview = application.handle(
        make_event(text="/curate", reply_text="A TLB caches recently used address translations.")
    )

    assert isinstance(preview, PresentedReply)
    assert "user-selected Telegram reply" in preview.text
    assert SourceRepository(database).list_all() == []
    assert "Saved curated note to Inbox" in application.handle(make_event(text="/approve_action 1"))
    source = SourceRepository(database).list_all()[0]
    assert "Origin: user-selected Telegram reply" in source.path.read_text(encoding="utf-8")


def test_telegram_can_synthesize_a_replied_discussion_locally_before_review(tmp_path: Path) -> None:
    database = tmp_path / "steward.db"; initialize_database(database)
    activity = ActivityService(database); proposals = ActionProposalRepository(database)
    capture = InboxCaptureService(tmp_path / "vault" / "inbox", SourceRepository(database), SourceFragmentRepository(database), activity)

    class LocalModel:
        def __init__(self) -> None: self.calls: list[str] = []
        def generate(self, *, instructions: str, input_text: str) -> str:
            assert "Do not introduce new facts" in instructions
            self.calls.append(input_text)
            return "# TLB\n\n- Caches address translations."

    local = LocalModel()
    application = StewardEventApplication(
        StewardQuestionApplication(FakeGraph()), StewardCaptureApplication(type("Capture", (), {})()),
        curated_note_application=StewardCuratedNoteApplication(proposals, activity, local_model=local),
        action_proposal_application=StewardActionProposalApplication(
            proposals, ActionProposalService(proposals, WorkspaceRepository(database), activity),
            activity_service=activity, capture_service=capture,
        ),
    )

    preview = application.handle(make_event(text="/curate_synthesize", reply_text="A TLB caches translations."))

    assert isinstance(preview, PresentedReply)
    assert "local model" in preview.text
    assert local.calls == ["A TLB caches translations."]
    assert SourceRepository(database).list_all() == []
    assert "Saved curated note to Inbox" in application.handle(make_event(text="/approve_action 1"))
    saved = SourceRepository(database).list_all()[0].path.read_text(encoding="utf-8")
    assert "Origin: model-synthesized user-selected Telegram reply (local model)" in saved
    assert "Caches address translations" in saved


def test_telegram_external_curate_synthesis_requires_an_explicit_choice(tmp_path: Path) -> None:
    database = tmp_path / "steward.db"; initialize_database(database)
    activity = ActivityService(database); proposals = ActionProposalRepository(database)

    class Model:
        def __init__(self, result: str) -> None: self.result = result; self.calls = 0
        def generate(self, *, instructions: str, input_text: str) -> str:
            self.calls += 1
            return self.result

    local = Model("# Local")
    external = Model("# External")
    application = StewardEventApplication(
        StewardQuestionApplication(FakeGraph()), StewardCaptureApplication(type("Capture", (), {})()),
        curated_note_application=StewardCuratedNoteApplication(
            proposals, activity, local_model=local, external_model=external,
        ),
    )

    preview = application.handle(make_event(text="/curate_synthesize external", reply_text="A point."))

    assert isinstance(preview, PresentedReply)
    assert "external model" in preview.text
    assert local.calls == 0 and external.calls == 1
    assert proposals.get(1).payload["text"] == "# External"


def test_curate_requires_a_replied_to_text_message(tmp_path: Path) -> None:
    database = tmp_path / "steward.db"; initialize_database(database)
    application = StewardEventApplication(
        StewardQuestionApplication(FakeGraph()), StewardCaptureApplication(type("Capture", (), {})()),
        curated_note_application=StewardCuratedNoteApplication(
            ActionProposalRepository(database), ActivityService(database)
        ),
    )

    assert application.handle(make_event(text="/curate")) == (
        "Reply to a text discussion message with /curate to stage it as a curated note."
    )


def test_telegram_workspace_link_is_reviewed_and_never_moves_the_source(tmp_path: Path) -> None:
    database = tmp_path / "steward.db"; initialize_database(database)
    now = datetime(2026, 9, 10, tzinfo=UTC)
    source_path = tmp_path / "note.md"; source_path.write_text("# Note", encoding="utf-8")
    sources = SourceRepository(database)
    sources.add(Source(None, source_path, "a" * 64, SourceType.MARKDOWN, 6, now, now, now))
    activity = ActivityService(database); workspaces = WorkspaceRepository(database)
    workspace = WorkspaceService(workspaces, activity).create("CS3210")
    proposals = ActionProposalRepository(database)
    application = StewardEventApplication(
        StewardQuestionApplication(FakeGraph()), StewardCaptureApplication(type("Capture", (), {})()),
        workspace_link_application=StewardWorkspaceLinkApplication(proposals, workspaces, sources, activity),
        action_proposal_application=StewardActionProposalApplication(
            proposals, ActionProposalService(proposals, workspaces, activity), activity_service=activity,
            workspace_repository=workspaces, source_repository=sources,
        ),
    )

    preview = application.handle(make_event(text="/propose_link_source 1 1"))
    assert isinstance(preview, PresentedReply)
    assert "No file will move" in preview.text
    assert application.handle(make_event(text="/approve_action 1")) == "Source 1 linked to workspace 1. No file moved."
    assert source_path.is_file()


def test_telegram_travel_reference_is_reviewed_and_grounded(tmp_path: Path) -> None:
    database = tmp_path / "steward.db"; initialize_database(database)
    now = datetime(2026, 9, 10, tzinfo=UTC)
    source_path = tmp_path / "trip.md"; source_path.write_text("Booking: https://example.com/ABC", encoding="utf-8")
    sources = SourceRepository(database)
    source = sources.add(Source(None, source_path, "a" * 64, SourceType.MARKDOWN, 32, now, now, now))
    fragments = SourceFragmentRepository(database)
    fragment = fragments.replace_for_source(ExtractionResult(source.id or 0, (
        SourceFragment(None, source.id or 0, None, 0, "Booking: https://example.com/ABC", "lines 1-1"),
    )))[0]
    records = RecordService(database)
    record = records.create_travel_record(TravelRecord(None, source.id or 0, "SQ638", None, None, None, None, None))
    activity = ActivityService(database); proposals = ActionProposalRepository(database)
    application = StewardEventApplication(
        StewardQuestionApplication(FakeGraph()), StewardCaptureApplication(type("Capture", (), {})()),
        record_application=StewardRecordApplication(records, fragments, proposals, activity),
        action_proposal_application=StewardActionProposalApplication(
            proposals, ActionProposalService(proposals, WorkspaceRepository(database), activity),
            record_service=records, activity_service=activity,
        ),
    )

    preview = application.handle(make_event(text=(
        f"/propose_travel_reference {record.id} booking_url {fragment.id} https://example.com/ABC"
    )))

    assert isinstance(preview, PresentedReply)
    assert "record remains unchanged" in preview.text
    assert records.list_references(record.id or 0) == ()
    assert application.handle(make_event(text="/approve_action 1")) == (
        "Travel reference 1 added to record 1: booking_url (fragment 1)."
    )
    assert "booking_url = https://example.com/ABC (fragment 1)" in application.handle(
        make_event(text=f"/travel_references {record.id}")
    )
    assert activity.list_recent()[1].event_type is ActivityType.TRAVEL_REFERENCE_ADDED


def test_telegram_reextract_is_reviewed_and_preserves_the_original(tmp_path: Path) -> None:
    database = tmp_path / "steward.db"
    initialize_database(database)
    now = datetime(2026, 9, 10, tzinfo=UTC)
    source_path = tmp_path / "notes.md"
    original = "# TLB\n\nA TLB caches translations.\n"
    source_path.write_text(original, encoding="utf-8")
    sources = SourceRepository(database)
    source = sources.add(
        Source(None, source_path, "a" * 64, SourceType.MARKDOWN, len(original), now, now, now)
    )
    fragments = SourceFragmentRepository(database)
    fragments.replace_for_source(
        ExtractionResult(source.id or 0, (SourceFragment(None, source.id or 0, None, 0, "stale", "lines 1-1"),))
    )
    activity = ActivityService(database)
    proposals = ActionProposalRepository(database)
    application = StewardEventApplication(
        StewardQuestionApplication(FakeGraph()),
        StewardCaptureApplication(type("Capture", (), {})()),
        action_proposal_application=StewardActionProposalApplication(
            proposals,
            ActionProposalService(proposals, WorkspaceRepository(database), activity),
            activity_service=activity,
            source_repository=sources,
            source_service=SourceService(sources, fragments, MarkdownExtractor()),
        ),
    )

    preview = application.handle(make_event(text=f"/propose_reextract {source.id}"))

    assert isinstance(preview, PresentedReply)
    assert "original file will not change" in preview.text
    assert fragments.list_for_source(source.id or 0)[0].text == "stale"

    accepted = application.handle(make_event(text="/approve_action 1"))

    assert accepted == "Refreshed derived text for source 1: 1 fragments. Original unchanged."
    assert source_path.read_text(encoding="utf-8") == original
    assert "A TLB caches translations." in fragments.list_for_source(source.id or 0)[0].text
    assert proposals.get(1).status == "accepted"
    assert activity.list_recent()[1].event_type is ActivityType.SOURCE_REEXTRACTED


def test_telegram_reextract_keeps_a_failed_refresh_pending(tmp_path: Path) -> None:
    database = tmp_path / "steward.db"
    initialize_database(database)
    activity = ActivityService(database)
    proposals = ActionProposalRepository(database)
    sources = SourceRepository(database)
    application = StewardActionProposalApplication(
        proposals,
        ActionProposalService(proposals, WorkspaceRepository(database), activity),
        activity_service=activity,
        source_repository=sources,
        source_service=SourceService(sources, SourceFragmentRepository(database), MarkdownExtractor()),
    )

    assert application.handle_command(make_event(text="/propose_reextract 99")) == "Source 99 was not found."
    proposal = proposals.add(application.REEXTRACT_SOURCE, {"source_id": "99"})

    assert application.handle_command(make_event(text=f"/approve_action {proposal.id}")) == (
        "Could not refresh derived text: Source 99 was not found."
    )
    assert proposals.get(proposal.id or 0).status == "pending"


def test_telegram_semantic_rebuild_is_reviewed_and_uses_no_source_paths(tmp_path: Path) -> None:
    database = tmp_path / "steward.db"
    initialize_database(database)
    activity = ActivityService(database)
    proposals = ActionProposalRepository(database)
    calls: list[str] = []
    application = StewardActionProposalApplication(
        proposals,
        ActionProposalService(proposals, WorkspaceRepository(database), activity),
        activity_service=activity,
        semantic_index_rebuilder=lambda: (calls.append("rebuilt") or 7),
    )

    preview = application.handle_command(make_event(text="/propose_rebuild_index"))

    assert isinstance(preview, PresentedReply)
    assert "original files will not change" in preview.text
    assert calls == []
    assert application.handle_command(make_event(text="/approve_action 1")) == (
        "Rebuilt local semantic index for 7 fragments. Original files unchanged."
    )
    assert calls == ["rebuilt"]
    assert proposals.get(1).status == "accepted"
    assert activity.list_recent()[1].event_type is ActivityType.SEMANTIC_INDEX_REBUILT


def test_telegram_semantic_rebuild_failure_keeps_review_pending(tmp_path: Path) -> None:
    database = tmp_path / "steward.db"
    initialize_database(database)
    activity = ActivityService(database)
    proposals = ActionProposalRepository(database)

    def unavailable() -> int:
        raise RuntimeError("C:/private/cache/model is unavailable")

    application = StewardActionProposalApplication(
        proposals,
        ActionProposalService(proposals, WorkspaceRepository(database), activity),
        activity_service=activity,
        semantic_index_rebuilder=unavailable,
    )
    application.handle_command(make_event(text="/propose_rebuild_index"))

    response = application.handle_command(make_event(text="/approve_action 1"))

    assert response == (
        "Could not rebuild the local semantic index. Verify the local embedding model, then retry the pending proposal."
    )
    assert "C:/private" not in response
    assert proposals.get(1).status == "pending"


def test_telegram_integration_status_reveals_only_local_readiness(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("STEWARD_GOOGLE_CLIENT_SECRETS", "C:/private/client.json")
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "google-calendar-token.json").write_text("secret token", encoding="utf-8")
    application = StewardEventApplication(
        StewardQuestionApplication(FakeGraph()), StewardCaptureApplication(type("Capture", (), {})()),
        integration_status_application=StewardIntegrationStatusApplication(tmp_path),
    )

    response = application.handle(make_event(text="/integrations"))

    assert "OAuth client: configured locally" in response
    assert "Calendar: local token present" in response
    assert "Drive: needs local browser authorization" in response
    assert "secret token" not in response and "client.json" not in response


def test_source_pagination_exposes_only_bounded_follow_up_commands(tmp_path: Path) -> None:
    database_path = tmp_path / "steward.db"
    initialize_database(database_path)
    sources = SourceRepository(database_path)
    now = datetime(2026, 9, 8, tzinfo=UTC)
    for identifier in range(11):
        path = tmp_path / f"note-{identifier}.md"
        path.write_text("note", encoding="utf-8")
        sources.add(Source(None, path, f"{identifier:064x}", SourceType.MARKDOWN, 4, now, now, now))
    fragments = SourceFragmentRepository(database_path)
    reads = StewardReadApplication(
        sources, fragments, LexicalSearchService(sources, fragments), WorkspaceRepository(database_path),
        ActivityService(database_path), tmp_path / "inbox"
    )

    response = reads.sources(1)

    assert isinstance(response, PresentedReply)
    assert response.actions[0].label == "Next"
    assert response.actions[0].command == "/sources 2"


def test_attachment_is_provisional_unless_its_caption_uses_save(tmp_path: Path) -> None:
    database_path = tmp_path / "steward.db"
    initialize_database(database_path)
    sources = SourceRepository(database_path)
    activity = ActivityService(database_path)
    capture_service = InboxCaptureService(
        tmp_path / "vault" / "inbox", sources, SourceFragmentRepository(database_path), activity
    )
    capture = StewardCaptureApplication(capture_service)
    provisional = StewardProvisionalIntakeApplication(
        ProvisionalIntakeService(
            tmp_path / ".steward" / "cache" / "intake",
            ProvisionalIntakeRepository(database_path),
            capture_service,
            activity,
            PrivacyService(database_path),
        )
    )
    application = StewardEventApplication(
        StewardQuestionApplication(FakeGraph()), capture, provisional_intake_application=provisional
    )
    original = tmp_path / "notes.pdf"
    original.write_bytes(b"pdf")
    attachment_event = IncomingEvent(
        "telegram:attachment", "telegram", "100", "12", None,
        datetime(2026, 9, 9, tzinfo=UTC), None, ("notes.pdf",),
    )

    proposed = application.handle_file(attachment_event, original)

    assert isinstance(proposed, PresentedReply)
    assert any(action.command == "/intake_analysis 1 external" for action in proposed.actions)
    assert sources.list_all() == []
    selected = application.handle(
        IncomingEvent(
            "telegram:analysis", "telegram", "100", "13", None,
            datetime(2026, 9, 9, tzinfo=UTC), "/intake_analysis 1 external",
        )
    )
    assert isinstance(selected, PresentedReply)
    assert "external" in selected.text
    accepted = application.handle(
        IncomingEvent(
            "telegram:accept", "telegram", "100", "13", None,
            datetime(2026, 9, 9, tzinfo=UTC), "/intake_accept 1",
        )
    )
    assert "Saved to Inbox" in accepted
    assert len(sources.list_all()) == 1
    assert PrivacyService(database_path).rule_for(1) is PrivacyRule.EXTERNAL_ALLOWED


def test_provisional_intake_context_command_updates_without_saving(tmp_path: Path) -> None:
    database_path = tmp_path / "steward.db"
    initialize_database(database_path)
    sources = SourceRepository(database_path)
    activity = ActivityService(database_path)
    capture_service = InboxCaptureService(tmp_path / "vault" / "inbox", sources, activity_service=activity)
    provisional = StewardProvisionalIntakeApplication(
        ProvisionalIntakeService(
            tmp_path / ".steward" / "cache" / "intake", ProvisionalIntakeRepository(database_path),
            capture_service, activity, PrivacyService(database_path),
        )
    )
    original = tmp_path / "notes.pdf"
    original.write_bytes(b"pdf")
    event = IncomingEvent(
        "telegram:attachment", "telegram", "100", "12", None,
        datetime(2026, 9, 9, tzinfo=UTC), None, ("notes.pdf",),
    )
    provisional.begin_file(event, original)

    updated = provisional.handle_command(
        IncomingEvent(
            "telegram:context", "telegram", "100", "13", None,
            datetime(2026, 9, 9, tzinfo=UTC), "/intake_context 1 CS3210 assignment",
        )
    )

    assert isinstance(updated, PresentedReply)
    assert "CS3210 assignment" in updated.text
    assert sources.list_all() == []


def test_event_application_routes_downloaded_document_to_capture_service(tmp_path: Path) -> None:
    class FileCapture:
        def __init__(self) -> None:
            self.received: tuple[IncomingEvent, Path] | None = None

        def capture_file(self, event: IncomingEvent, path: Path) -> CaptureResult:
            self.received = (event, path)
            return CaptureResult(source=type("Source", (), {"path": Path("vault/inbox/note.pdf")})(), duplicate=False)

    capture = FileCapture()
    application = StewardEventApplication(StewardQuestionApplication(FakeGraph()), StewardCaptureApplication(capture))
    document = tmp_path / "note.pdf"
    document.write_bytes(b"pdf")

    response = application.handle_file(make_event(text=None), document)

    assert response == "Saved to Inbox: note.pdf"
    assert capture.received == (make_event(text=None), document)


def test_telegram_capture_pauses_then_resumes_an_organization_approval(tmp_path: Path) -> None:
    database_path = tmp_path / ".steward" / "steward.db"
    initialize_database(database_path)
    sources = SourceRepository(database_path)
    activity = ActivityService(database_path)
    capture = StewardCaptureApplication(
        InboxCaptureService(tmp_path / "vault" / "inbox", sources, activity_service=activity)
    )
    workspaces = WorkspaceRepository(database_path)
    workspaces.create("Steward")
    proposals = OrganizationProposalRepository(database_path)
    approval_service = OrganizationApprovalService(
        proposals, sources, FileMutationService(sources, activity), activity
    )
    approval_application = StewardOrganizationApprovalApplication(
        proposals,
        workspaces,
        OrganizationApprovalThreadRepository(database_path),
        activity,
        build_organization_approval_graph(
            proposals,
            checkpointer=InMemorySaver(),
            review_proposal=approval_service.review,
        ),
    )
    application = StewardEventApplication(
        StewardQuestionApplication(FakeGraph()),
        capture,
        organization_approval_application=approval_application,
    )
    uploaded = tmp_path / "Steward notes.md"
    uploaded.write_text("# Design", encoding="utf-8")
    capture_event = IncomingEvent(
        "telegram:99",
        "telegram",
        "100",
        "11",
        None,
        datetime(2026, 9, 8, tzinfo=UTC),
        "/save",
        attachments=("Steward notes.md",),
    )

    paused = application.handle_file(capture_event, uploaded)

    assert isinstance(paused, PresentedReply)
    assert "Organization proposal 1" in paused.text
    assert [action.command for action in paused.actions] == [
        "/organization_accept 1", "/organization_reject 1"
    ]
    assert not (tmp_path / "vault" / "projects" / "Steward").exists()

    accepted = application.handle(
        IncomingEvent(
            "telegram:100",
            "telegram",
            "100",
            "12",
            None,
            datetime(2026, 9, 8, tzinfo=UTC),
            "/organization_accept 1",
        )
    )

    assert accepted == "Proposal 1 accepted."
    assert (tmp_path / "vault" / "projects" / "Steward" / "telegram-100-11-Steward-notes.md").is_file()
    assert proposals.get(1).status == "accepted"


def test_telegram_organization_approval_survives_a_process_restart(tmp_path: Path) -> None:
    database = tmp_path / "steward.db"; checkpoints = tmp_path / "checkpoints.db"; initialize_database(database)
    inbox = tmp_path / "vault" / "inbox"; inbox.mkdir(parents=True)
    source_path = inbox / "Steward-design.md"; source_path.write_text("# Design", encoding="utf-8")
    now = datetime(2026, 9, 10, tzinfo=UTC)
    sources = SourceRepository(database)
    source = sources.add(Source(None, source_path, "a" * 64, SourceType.MARKDOWN, 8, now, now, now))
    workspaces = WorkspaceRepository(database); workspaces.create("Steward")
    proposals = OrganizationProposalRepository(database); activity = ActivityService(database)
    service = OrganizationApprovalService(proposals, sources, FileMutationService(sources, activity), activity)
    first_connection = sqlite3.connect(checkpoints, check_same_thread=False)
    first = StewardOrganizationApprovalApplication(
        proposals, workspaces, OrganizationApprovalThreadRepository(database), activity,
        build_organization_approval_graph(proposals, checkpointer=SqliteSaver(first_connection), review_proposal=service.review),
        source_repository=sources, inbox_dir=inbox,
    )

    paused = first.begin(make_event(text="/save"), CaptureResult(source, duplicate=False))

    assert isinstance(paused, PresentedReply)
    first_connection.close()
    restarted_connection = sqlite3.connect(checkpoints, check_same_thread=False)
    restarted = StewardOrganizationApprovalApplication(
        proposals, workspaces, OrganizationApprovalThreadRepository(database), activity,
        build_organization_approval_graph(proposals, checkpointer=SqliteSaver(restarted_connection), review_proposal=service.review),
        source_repository=sources, inbox_dir=inbox,
    )
    accepted = restarted.handle_decision(make_event(text="/organization_accept 1"))
    restarted_connection.close()

    assert accepted == "Proposal 1 accepted."
    assert proposals.get(1).status == "accepted"
    assert (tmp_path / "vault" / "projects" / "Steward" / "Steward-design.md").is_file()


def test_organize_inbox_creates_one_durable_proposal_before_any_move(tmp_path: Path) -> None:
    database_path = tmp_path / "steward.db"
    initialize_database(database_path)
    inbox = tmp_path / "vault" / "inbox"
    inbox.mkdir(parents=True)
    source_path = inbox / "CS3210-openmp.md"
    source_path.write_text("notes", encoding="utf-8")
    now = datetime(2026, 9, 9, tzinfo=UTC)
    sources = SourceRepository(database_path)
    source = sources.add(Source(None, source_path, "a" * 64, SourceType.MARKDOWN, 5, now, now, now))
    workspaces = WorkspaceRepository(database_path)
    workspaces.create("CS3210")
    proposals = OrganizationProposalRepository(database_path)
    activity = ActivityService(database_path)
    organization = StewardOrganizationApprovalApplication(
        proposals,
        workspaces,
        OrganizationApprovalThreadRepository(database_path),
        activity,
        type("Graph", (), {"invoke": lambda *_args, **_kwargs: {"__interrupt__": ()}})(),
        source_repository=sources,
        inbox_dir=inbox,
    )
    application = StewardEventApplication(
        StewardQuestionApplication(FakeGraph()),
        StewardCaptureApplication(type("Capture", (), {})()),
        organization_approval_application=organization,
    )

    response = application.handle(make_event(text="organize my inbox"))

    assert isinstance(response, PresentedReply)
    assert "Organization proposal 1" in response.text
    assert proposals.get(1).source_id == source.id
    assert source_path.is_file()


def test_pending_telegram_approval_does_not_treat_other_text_as_a_question(tmp_path: Path) -> None:
    database_path = tmp_path / "steward.db"
    initialize_database(database_path)
    threads = OrganizationApprovalThreadRepository(database_path)
    proposals = OrganizationProposalRepository(database_path)
    source_path = tmp_path / "note.md"; source_path.write_text("note", encoding="utf-8")
    now = datetime(2026, 9, 8, tzinfo=UTC)
    source = SourceRepository(database_path).add(
        Source(None, source_path.resolve(), "a" * 64, SourceType.MARKDOWN, 4, now, now, now)
    )
    proposal_id = proposals.add(
        OrganizationProposal(
            None, source.id or 0, "keep_in_inbox", None, None, "Keep", 0.0
        )
    )
    threads.start("telegram", "100", proposal_id, "approval:telegram:100:1")
    app = StewardOrganizationApprovalApplication(
        proposals,
        WorkspaceRepository(database_path),
        threads,
        ActivityService(database_path),
        type("Graph", (), {"invoke": lambda *_args, **_kwargs: {}})(),
    )

    response = app.handle_decision(make_event(text="What is a TLB?"))

    assert response == "Proposal 1 is awaiting your decision. Reply exactly `accept` or `reject`."


def test_uncertain_capture_can_be_refined_with_existing_workspace_context(tmp_path: Path) -> None:
    database_path = tmp_path / "steward.db"
    initialize_database(database_path)
    now = datetime(2026, 9, 8, tzinfo=UTC)
    source_path = tmp_path / "vault" / "inbox" / "unrelated.md"; source_path.parent.mkdir(parents=True)
    source_path.write_text("note", encoding="utf-8")
    sources = SourceRepository(database_path)
    source = sources.add(
        Source(None, source_path.resolve(), "b" * 64, SourceType.MARKDOWN, 4, now, now, now)
    )
    proposals = OrganizationProposalRepository(database_path)
    threads = OrganizationApprovalThreadRepository(database_path)
    activity = ActivityService(database_path)
    workspaces = WorkspaceRepository(database_path)
    workspaces.create("CS3210")
    approval = OrganizationApprovalService(
        proposals, sources, FileMutationService(sources, activity), activity, workspaces
    )
    app = StewardOrganizationApprovalApplication(
        proposals,
        workspaces,
        threads,
        activity,
        build_organization_approval_graph(
            proposals, checkpointer=InMemorySaver(), review_proposal=approval.review
        ),
        source_repository=sources,
    )

    response = app.begin(make_event(text="/save unrelated"), CaptureResult(source, duplicate=False))

    assert isinstance(response, PresentedReply)
    assert "leave the original in Inbox" in response.text
    assert proposals.get(1).status == "pending"
    revised = app.handle_decision(make_event(text="/organization_context 1 CS3210 lecture notes"))
    assert isinstance(revised, PresentedReply)
    assert "Organization proposal 2" in revised.text
    assert proposals.get(1).status == "rejected"
    accepted = app.handle_decision(make_event(text="/organization_accept 2"))
    assert accepted == "Proposal 2 accepted."
    assert (tmp_path / "vault" / "projects" / "CS3210" / "unrelated.md").is_file()


def test_uncertain_capture_can_propose_a_new_workspace_then_move_after_review(tmp_path: Path) -> None:
    database = tmp_path / "steward.db"; initialize_database(database)
    now = datetime(2026, 9, 10, tzinfo=UTC)
    inbox = tmp_path / "vault" / "inbox"; inbox.mkdir(parents=True)
    path = inbox / "distributed-systems.md"; path.write_text("note", encoding="utf-8")
    sources = SourceRepository(database)
    source = sources.add(Source(None, path, "e" * 64, SourceType.MARKDOWN, 4, now, now, now))
    workspaces = WorkspaceRepository(database); proposals = OrganizationProposalRepository(database); activity = ActivityService(database)
    approval = OrganizationApprovalService(proposals, sources, FileMutationService(sources, activity), activity, workspaces)
    app = StewardOrganizationApprovalApplication(
        proposals, workspaces, OrganizationApprovalThreadRepository(database), activity,
        build_organization_approval_graph(proposals, checkpointer=InMemorySaver(), review_proposal=approval.review),
        source_repository=sources,
    )

    app.begin(make_event(text="/save"), CaptureResult(source, duplicate=False))
    revised = app.handle_decision(make_event(text="/organization_new_workspace 1 Distributed Systems"))

    assert isinstance(revised, PresentedReply)
    assert proposals.get(2).workspace_name == "Distributed Systems"
    assert WorkspaceRepository(database).list_all() == []
    assert app.handle_decision(make_event(text="/organization_accept 2")) == "Proposal 2 accepted."
    assert [workspace.name for workspace in WorkspaceRepository(database).list_all()] == ["Distributed Systems"]
    assert (tmp_path / "vault" / "projects" / "Distributed Systems" / "distributed-systems.md").is_file()


def test_context_revised_organization_proposal_survives_a_restart(tmp_path: Path) -> None:
    database = tmp_path / "steward.db"; checkpoints = tmp_path / "checkpoints.db"; initialize_database(database)
    inbox = tmp_path / "vault" / "inbox"; inbox.mkdir(parents=True)
    source_path = inbox / "unrelated.md"; source_path.write_text("note", encoding="utf-8")
    now = datetime(2026, 9, 10, tzinfo=UTC)
    sources = SourceRepository(database)
    source = sources.add(Source(None, source_path, "f" * 64, SourceType.MARKDOWN, 4, now, now, now))
    workspaces = WorkspaceRepository(database); workspaces.create("CS3210")
    proposals = OrganizationProposalRepository(database); activity = ActivityService(database)
    approval = OrganizationApprovalService(proposals, sources, FileMutationService(sources, activity), activity)
    first_connection = sqlite3.connect(checkpoints, check_same_thread=False)
    first = StewardOrganizationApprovalApplication(
        proposals, workspaces, OrganizationApprovalThreadRepository(database), activity,
        build_organization_approval_graph(proposals, checkpointer=SqliteSaver(first_connection), review_proposal=approval.review),
        source_repository=sources,
    )

    first.begin(make_event(text="/save"), CaptureResult(source, duplicate=False))
    revised = first.handle_decision(make_event(text="/organization_context 1 CS3210"))
    assert isinstance(revised, PresentedReply)
    first_connection.close()
    restarted_connection = sqlite3.connect(checkpoints, check_same_thread=False)
    restarted = StewardOrganizationApprovalApplication(
        proposals, workspaces, OrganizationApprovalThreadRepository(database), activity,
        build_organization_approval_graph(proposals, checkpointer=SqliteSaver(restarted_connection), review_proposal=approval.review),
        source_repository=sources,
    )

    assert restarted.handle_decision(make_event(text="/organization_accept 2")) == "Proposal 2 accepted."
    restarted_connection.close()
    assert (tmp_path / "vault" / "projects" / "CS3210" / "unrelated.md").is_file()


def test_capture_uses_an_injected_proposal_builder_before_pausing_for_approval(tmp_path: Path) -> None:
    database_path = tmp_path / "steward.db"
    initialize_database(database_path)
    now = datetime(2026, 9, 8, tzinfo=UTC)
    inbox = tmp_path / "vault" / "inbox" / "notes.md"
    inbox.parent.mkdir(parents=True)
    inbox.write_text("parallel computing notes", encoding="utf-8")
    source = SourceRepository(database_path).add(
        Source(None, inbox.resolve(), "c" * 64, SourceType.MARKDOWN, 24, now, now, now)
    )
    workspace = WorkspaceRepository(database_path).create("CS3210")
    captured: dict[str, object] = {}

    def builder(candidate, workspaces):
        captured["source"] = candidate
        captured["workspaces"] = workspaces
        return OrganizationProposal(
            None,
            candidate.id or 0,
            "move_to_workspace",
            workspace.id,
            tmp_path / "vault" / "projects" / "CS3210" / candidate.path.name,
            "The extracted notes discuss CS3210.",
            0.8,
        )

    proposals = OrganizationProposalRepository(database_path)
    app = StewardOrganizationApprovalApplication(
        proposals,
        WorkspaceRepository(database_path),
        OrganizationApprovalThreadRepository(database_path),
        ActivityService(database_path),
        type("Graph", (), {"invoke": lambda *_args, **_kwargs: {"__interrupt__": ()}})(),
        proposal_builder=builder,
    )

    response = app.begin(make_event(text="/save"), CaptureResult(source, duplicate=False))

    assert captured["source"] == source
    assert captured["workspaces"] == [workspace]
    assert isinstance(response, PresentedReply)
    assert "Organization proposal 1" in response.text
    assert proposals.get(1).rationale == "The extracted notes discuss CS3210."


def test_telegram_can_list_and_explicitly_review_a_pending_action_proposal(tmp_path: Path) -> None:
    database_path = tmp_path / "steward.db"
    initialize_database(database_path)
    activity = ActivityService(database_path)
    repository = ActionProposalRepository(database_path)
    service = ActionProposalService(repository, WorkspaceRepository(database_path), activity)
    proposal, _ = service.propose_workspace_creation("Compiler Project")
    assert proposal is not None and proposal.id is not None
    app = StewardEventApplication(
        StewardQuestionApplication(FakeGraph()),
        StewardCaptureApplication(type("Capture", (), {})()),
        action_proposal_application=StewardActionProposalApplication(repository, service),
    )

    listed = app.handle(make_event(text="/action_proposals"))
    accepted = app.handle(make_event(text=f"/approve_action {proposal.id}"))

    assert f"{proposal.id}: create_workspace" in listed
    assert accepted == (
        f"Action proposal {proposal.id} accepted. Workspace 1: Compiler Project is available."
    )
    assert WorkspaceRepository(database_path).list_all()[0].name == "Compiler Project"


def test_telegram_action_review_requires_an_explicit_numeric_command(tmp_path: Path) -> None:
    database_path = tmp_path / "steward.db"
    initialize_database(database_path)
    repository = ActionProposalRepository(database_path)
    service = ActionProposalService(
        repository, WorkspaceRepository(database_path), ActivityService(database_path)
    )
    app = StewardActionProposalApplication(repository, service)

    assert app.handle_command(make_event(text="/approve_action@steward_bot please")) == (
        "Use /approve_action followed by a numeric proposal ID."
    )


def test_telegram_calendar_proposal_never_falls_through_to_workspace_review(tmp_path: Path) -> None:
    database_path = tmp_path / "steward.db"; initialize_database(database_path)
    now = datetime(2026, 9, 8, tzinfo=UTC)
    source_path = tmp_path / "flight.pdf"; source_path.write_bytes(b"pdf")
    source = SourceRepository(database_path).add(
        Source(None, source_path, "a" * 64, SourceType.PDF, 3, now, now, now)
    )
    record = RecordService(database_path).create_travel_record(
        TravelRecord(None, source.id or 0, "SQ638", "Singapore", "Tokyo", now, now.replace(hour=2), None)
    )
    repository = ActionProposalRepository(database_path)
    calendar_proposals = CalendarEventProposalService(
        repository, RecordService(database_path), ActivityService(database_path)
    )
    proposal = calendar_proposals.propose_travel_event(record.id or 0)
    app = StewardActionProposalApplication(
        repository,
        ActionProposalService(repository, WorkspaceRepository(database_path), ActivityService(database_path)),
        calendar_proposals,
    )

    response = app.handle_command(make_event(text=f"/approve_action {proposal.id}"))

    assert response == "Calendar authorization is required to accept this proposal."
    assert repository.get(proposal.id or 0).status == "pending"


def test_telegram_can_create_a_pending_calendar_proposal_without_writing(tmp_path: Path) -> None:
    database_path = tmp_path / "steward.db"; initialize_database(database_path)
    now = datetime(2026, 9, 9, tzinfo=UTC)
    source_path = tmp_path / "flight.pdf"; source_path.write_bytes(b"pdf")
    source = SourceRepository(database_path).add(
        Source(None, source_path, "a" * 64, SourceType.PDF, 3, now, now, now)
    )
    records = RecordService(database_path)
    record = records.create_travel_record(
        TravelRecord(None, source.id or 0, "SQ638", "Singapore", "Tokyo", now, now.replace(hour=2), None)
    )
    repository = ActionProposalRepository(database_path)
    app = StewardActionProposalApplication(
        repository,
        ActionProposalService(repository, WorkspaceRepository(database_path), ActivityService(database_path)),
        CalendarEventProposalService(repository, records, ActivityService(database_path)),
    )

    response = app.handle_command(make_event(text=f"/calendar_travel {record.id}"))

    assert isinstance(response, PresentedReply)
    assert "No Calendar event has been created" in response.text
    assert response.actions[0].command == "/approve_action 1"


def test_telegram_can_create_a_reviewed_task_calendar_proposal(tmp_path: Path) -> None:
    database_path = tmp_path / "steward.db"; initialize_database(database_path)
    activity = ActivityService(database_path)
    tasks = TaskService(database_path)
    task = tasks.create("Submit CS3210 lab", due_at=datetime(2026, 9, 18, 15, 59, tzinfo=UTC))
    repository = ActionProposalRepository(database_path)
    app = StewardActionProposalApplication(
        repository,
        ActionProposalService(repository, WorkspaceRepository(database_path), activity),
        CalendarEventProposalService(repository, RecordService(database_path), activity, tasks),
    )

    response = app.handle_command(make_event(text=f"/calendar_task {task.id}"))

    assert isinstance(response, PresentedReply)
    assert "task 1" in response.text
    assert "No Calendar event has been created" in response.text
    assert repository.get(1).action_type == "create_calendar_task_event"


def test_telegram_can_explicitly_import_one_drive_file() -> None:
    class Importer:
        def __init__(self) -> None:
            self.file_ids: list[str] = []

        def import_file(self, file_id: str) -> CaptureResult:
            self.file_ids.append(file_id)
            return CaptureResult(
                type("Source", (), {"path": Path("vault/inbox/drive-import-file-42-note.pdf")})(),
                False,
            )

    importer = Importer()
    application = StewardEventApplication(
        StewardQuestionApplication(FakeGraph()),
        StewardCaptureApplication(type("Capture", (), {})()),
        drive_import_application=StewardDriveImportApplication(importer),
    )

    response = application.handle(make_event(text="/drive_import file-42"))

    assert importer.file_ids == ["file-42"]
    assert response == "Imported Drive file to Inbox: drive-import-file-42-note.pdf"


def test_telegram_drive_import_requires_an_explicit_single_file_id() -> None:
    application = StewardDriveImportApplication(None)

    assert application.handle_command(make_event(text="/drive_import")) == (
        "Use /drive_import followed by one Google Drive file ID."
    )
    assert application.handle_command(make_event(text="/drive_import first second")) == (
        "Use /drive_import followed by one Google Drive file ID."
    )
    assert application.handle_command(make_event(text="/drive_import file-42")) == (
        "Drive import is not configured on this Steward process. "
        "Set STEWARD_GOOGLE_CLIENT_SECRETS, authorize Drive, then try again."
    )


def test_telegram_drive_search_offers_explicit_individual_imports() -> None:
    class Importer:
        def search(self, query):
            assert query == "parallel"
            return (type("DriveFile", (), {"id": "file-42", "name": "Parallel Notes.pdf"})(),)

    response = StewardDriveImportApplication(Importer()).handle_command(make_event(text="/drive_search parallel"))

    assert isinstance(response, PresentedReply)
    assert "file-42: Parallel Notes.pdf" in response.text
    assert response.actions[0].command == "/drive_import file-42"


def test_telegram_can_explicitly_import_one_gmail_message() -> None:
    class Importer:
        def import_message(self, message_id: str) -> CaptureResult:
            assert message_id == "mail-42"
            return CaptureResult(type("Source", (), {"path": Path("vault/inbox/gmail-import-mail-42.eml")})(), False)

    application = StewardGmailImportApplication(Importer())

    assert application.handle_command(make_event(text="/gmail_import mail-42")) == (
        "Imported Gmail message to Inbox: gmail-import-mail-42.eml"
    )
    assert application.handle_command(make_event(text="/gmail_import")) == (
        "Use /gmail_import followed by one Gmail message ID."
    )


def test_telegram_gmail_search_offers_explicit_individual_imports() -> None:
    class Importer:
        def search(self, query):
            assert query == "OpenMP"
            return (type("GmailMessage", (), {"id": "mail-42", "subject": "OpenMP assignment"})(),)

    response = StewardGmailImportApplication(Importer()).handle_command(make_event(text="/gmail_search OpenMP"))

    assert isinstance(response, PresentedReply)
    assert "mail-42: OpenMP assignment" in response.text
    assert response.actions[0].command == "/gmail_import mail-42"
