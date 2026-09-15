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
    StewardReviewInboxApplication,
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
from steward.calendar import CalendarEventProposalService, CalendarLinkRepository, CalendarService
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
from steward.reviews import ReviewContextRepository
from steward.knowledge import KnowledgeEnrichmentProposalRepository, KnowledgeService
from steward.knowledge_connector import KnowledgeConnector
from steward.roots import SourceRootRepository
from steward.privacy import PrivacyRule, PrivacyService
from steward.reviews import ReviewContextRepository
from steward.telegram import TelegramUpdateDeliveryRepository
from steward.tasks import TaskReminderService, TaskService
from steward.research import EphemeralResearchCardRepository, ResearchBundle, ResearchProviderError, ResearchRetentionService, ResearchSource
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


def test_question_application_includes_bounded_explicit_telegram_reply_context() -> None:
    graph = FakeGraph()

    StewardQuestionApplication(graph).handle(
        make_event(text="How does this relate?", reply_text="A TLB caches address translations.")
    )

    assert graph.inputs == [{
        "question": (
            "Reply context (user-supplied): A TLB caches address translations.\n\n"
            "Current question: How does this relate?"
        )
    }]


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

    assert isinstance(response, PresentedReply)
    assert response.title == "Answer from your saved material"
    assert response.text == (
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
    assert "/intake_accept ID" in help_text
    assert "/integrations" in help_text
    assert "/propose_unregister_source SOURCE_ID" in help_text
    assert "/metrics - aggregate local decisions" in help_text
    assert "/organization_proposals" in help_text
    assert "review-required writes" in help_text.casefold()
    assert "/record travel|receipt|warranty ID" in help_text
    assert "/set_privacy SOURCE_ID RULE (review required)" in help_text


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

    inbox_card = application.handle(make_event(text="/inbox"))
    assert isinstance(inbox_card, PresentedReply)
    assert inbox_card.title == "Inbox" and "openmp.md" in inbox_card.text
    search = application.handle(make_event(text="/search OpenMP"))
    assert isinstance(search, PresentedReply)
    assert search.title == "Search results" and "openmp.md" in search.text
    assert search.actions[0].label == "Open 1"
    assert "Recent activity" in application.handle(make_event(text="/activity"))
    source_details = application.handle(make_event(text="/source 1"))
    assert isinstance(source_details, PresentedReply)
    assert source_details.title == "openmp.md"
    assert str(tmp_path) not in source_details.text
    natural_inbox = application.handle(make_event(text="what is in my inbox"))
    assert isinstance(natural_inbox, PresentedReply)
    assert "openmp.md" in natural_inbox.text
    assert "Recent activity" in application.handle(make_event(text="show my recent activity"))
    natural_search = application.handle(make_event(text="find my notes on OpenMP"))
    assert isinstance(natural_search, PresentedReply)
    assert natural_search.title == "Search results"
    organize = application.handle(make_event(text="organize my inbox"))
    assert isinstance(organize, PresentedReply)
    assert organize.title == "Inbox"
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

    assert isinstance(semantic, PresentedReply) and isinstance(hybrid, PresentedReply)
    assert semantic.title == "Semantic search results" and "memory.md" in semantic.text
    assert "Similarity: 0.91" in semantic.text and str(tmp_path) not in semantic.text
    assert hybrid.title == "Hybrid search results" and "Match: 0.03" in hybrid.text


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


def test_metrics_reports_aggregate_activity_without_event_details(tmp_path: Path) -> None:
    database = tmp_path / "steward.db"; initialize_database(database)
    inbox = tmp_path / "vault" / "inbox"; inbox.mkdir(parents=True)
    activity = ActivityService(database)
    activity.record(ActivityType.ACTION_REJECTED, details="C:/private/secret-note.md")
    activity.record(ActivityType.ACTION_REJECTED, details="another private detail")
    reads = StewardReadApplication(
        SourceRepository(database), SourceFragmentRepository(database),
        LexicalSearchService(SourceRepository(database), SourceFragmentRepository(database)),
        WorkspaceRepository(database), activity, inbox,
    )

    response = reads.handle_command(make_event(text="/metrics"))

    assert response == "Local activity metrics:\naction_rejected: 2"
    assert "C:/private" not in response and "another private detail" not in response


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


def test_status_renders_only_injected_safe_runtime_labels(tmp_path: Path) -> None:
    database = tmp_path / "steward.db"; initialize_database(database)
    reads = StewardReadApplication(
        SourceRepository(database), SourceFragmentRepository(database),
        LexicalSearchService(SourceRepository(database), SourceFragmentRepository(database)),
        WorkspaceRepository(database), ActivityService(database), tmp_path,
        runtime_status=lambda: (
            "Operational database: available",
            "Conversation checkpoints: unavailable",
            "Authorized roots: 1 available, 0 missing, 0 disabled",
            "Model provider: local configured",
        ),
    )

    response = reads.status()

    assert "Operational database: available" in response
    assert "Conversation checkpoints: unavailable" in response
    assert "Model provider: local configured" in response
    assert str(tmp_path) not in response


def test_status_hides_runtime_callback_failures(tmp_path: Path) -> None:
    database = tmp_path / "steward.db"; initialize_database(database)

    def fail() -> tuple[str, ...]:
        raise RuntimeError(f"C:/private/{tmp_path.name}/database")

    reads = StewardReadApplication(
        SourceRepository(database), SourceFragmentRepository(database),
        LexicalSearchService(SourceRepository(database), SourceFragmentRepository(database)),
        WorkspaceRepository(database), ActivityService(database), tmp_path, runtime_status=fail,
    )

    response = reads.status()

    assert "Local runtime health: temporarily unavailable" in response
    assert "C:/private" not in response


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


def test_normal_question_prefers_the_configured_read_only_tool_agent() -> None:
    class ToolGraph:
        def __init__(self) -> None:
            self.calls = 0

        def invoke(self, input, config):
            self.calls += 1
            assert input["messages"][1].content == "What do I know about OpenMP?"
            assert config["configurable"]["thread_id"] == "tool-agent:telegram:100"
            return {"messages": [type("Final", (), {"content": "Found local OpenMP notes."})()]}

    graph = ToolGraph()
    application = StewardEventApplication(
        StewardQuestionApplication(FakeGraph()),
        StewardCaptureApplication(type("Capture", (), {})()),
        tool_agent_application=StewardToolAgentApplication(graph),
    )

    assert application.handle(make_event(text="What do I know about OpenMP?")) == "Found local OpenMP notes."
    assert graph.calls == 1


def test_tool_agent_uses_an_explicit_telegram_reply_as_bounded_context() -> None:
    class ToolGraph:
        def invoke(self, input, _config):
            assert input["messages"][1].content == (
                "Reply context (user-supplied): My CS3210 notes use OpenMP.\n\n"
                "Current question: Explain this further."
            )
            return {"messages": [type("Final", (), {"content": "Explained."})()]}

    response = StewardToolAgentApplication(ToolGraph()).handle_request(
        make_event(text="Explain this further.", reply_text="My CS3210 notes use OpenMP.")
    )

    assert response == "Explained."


def test_pending_review_pages_reach_older_items_and_recover_after_decisions(tmp_path: Path) -> None:
    database = tmp_path / "steward.db"
    initialize_database(database)
    actions = ActionProposalRepository(database)
    for index in range(10):
        actions.add("create_workspace", {"name": f"Workspace {index}"})
    reviews = StewardReviewInboxApplication(
        actions, OrganizationProposalRepository(database), SourceRepository(database),
    )
    first = reviews.handle_command(make_event(text="/pending"))
    next_button = next(action for action in first.actions if action.label == "Next")
    second = reviews.handle_command(make_event(text=next_button.command))
    reviewed = {
        action.command for card in (first, second) for action in card.actions
        if action.label.startswith("Review")
    }
    assert reviewed == {f"/review action {identifier}" for identifier in range(1, 11)}
    assert "10 decisions" in second.title
    for identifier in range(1, 10):
        actions.set_status(identifier, "rejected")
    refreshed = reviews.handle_command(make_event(text="/pending 2"))
    assert "Page 1 of 1" in refreshed.text
    assert refreshed.actions[0].command == "/review action 10"


def test_pending_review_inbox_keeps_colliding_domain_ids_distinct(tmp_path: Path) -> None:
    database = tmp_path / "steward.db"
    initialize_database(database)
    now = datetime(2026, 9, 10, tzinfo=UTC)
    source_path = tmp_path / "vault" / "inbox" / "resume.pdf"
    source_path.parent.mkdir(parents=True)
    source_path.write_text("resume", encoding="utf-8")
    sources = SourceRepository(database)
    source = sources.add(Source(None, source_path, "a" * 64, SourceType.PDF, 6, now, now, now))
    actions = ActionProposalRepository(database)
    action = actions.add("create_workspace", {"name": "Job Search"})
    organizations = OrganizationProposalRepository(database)
    organization = organizations.add(
        OrganizationProposal(None, source.id or 0, "keep_in_inbox", None, None, "No match yet.", 0.0, user_guidance="This is for my job search.")
    )
    reviews = StewardReviewInboxApplication(
        actions, organizations, sources, contexts=ReviewContextRepository(database)
    )
    event = make_event(text="/pending")

    pending = reviews.handle_command(event)

    assert isinstance(pending, PresentedReply)
    assert pending.title == "2 decisions waiting"
    assert "Create workspace Job Search" in pending.text
    assert "Organize resume.pdf" in pending.text
    assert {action.command for action in pending.actions} == {
        f"/review action {action.id}", f"/review organization {organization}",
    }
    detail = reviews.handle_command(make_event(text=f"/review organization {organization}"))
    assert isinstance(detail, PresentedReply)
    assert detail.title == "Organize resume.pdf"
    assert "No match yet." in detail.text
    assert "Your context: This is for my job search." in detail.text
    assert {action.command for action in detail.actions} == {
        f"/organization_accept {organization}", f"/organization_context {organization}",
        f"/organization_new_workspace {organization}", f"/organization_keep_inbox {organization}",
        f"/organization_reject {organization}",
    }
    followup = reviews.handle_followup(make_event(text="what is this proposal?"))
    assert isinstance(followup, PresentedReply)
    assert followup.title == "Organize resume.pdf"

    class QuestionMustNotRun:
        def invoke(self, input, config=None):
            raise AssertionError("a pending-review request must not enter retrieval")

    application = StewardEventApplication(
        StewardQuestionApplication(QuestionMustNotRun()),
        StewardCaptureApplication(type("Capture", (), {})()),
        review_inbox_application=reviews,
    )
    natural = application.handle(make_event(text="what are the proposals?"))
    assert isinstance(natural, PresentedReply)
    assert natural.title == "2 decisions waiting"

    draft = actions.add(StewardCuratedNoteApplication.CREATE_CURATED_NOTE, {
        "text": "# Reviewed draft\nA specific point.", "origin": "selected reply",
    })
    draft_card = reviews.handle_command(make_event(text=f"/review action {draft.id}"))
    assert "A specific point." in draft_card.text
    assert "selected reply" in draft_card.text
    assert actions.get(draft.id).status == "pending"
    correction = actions.add(StewardRecordApplication.CORRECT_TRAVEL_RECORD, {
        "record_id": "42", "field": "arrival", "value": "Osaka",
    })
    correction_card = reviews.handle_command(make_event(text=f"/review action {correction.id}"))
    assert "Record: 42" in correction_card.text and "Replacement: Osaka" in correction_card.text
    assert "not automatically source-evidenced" in correction_card.text

    fragments = SourceFragmentRepository(database)
    fragments.replace_for_source(ExtractionResult(source.id, (
        SourceFragment(None, source.id, None, 0, "Flight SQ638\nArrival: Tokyo", "page 1"),
    )))
    from steward.records import record_review_snapshot
    parts = [(part.id, part.text) for part in fragments.list_for_source(source.id)]
    preview = RecordService(database).propose_travel_record(source.id, parts)
    record_proposal = actions.add("create_travel_record", {
        "source_id": str(source.id), "snapshot": record_review_snapshot(preview, parts),
    })
    record_reviews = StewardReviewInboxApplication(
        actions, organizations, sources, records=RecordService(database), fragments=fragments,
    )
    record_card = record_reviews.handle_command(make_event(text=f"/review action {record_proposal.id}"))
    assert "flight_number: SQ638" in record_card.text and "arrival: Tokyo" in record_card.text
    assert "fragment" in record_card.text and "resume.pdf" in record_card.text
    assert str(tmp_path) not in record_card.text
    assert any(action.command == f"/source {source.id}" for action in record_card.actions)
    assert RecordService(database).list_travel_records() == []


def test_pending_task_review_uses_readable_offset_aware_schedule_labels(tmp_path: Path) -> None:
    database = tmp_path / "steward.db"; initialize_database(database)
    actions = ActionProposalRepository(database)
    proposal = actions.add(StewardTaskApplication.CREATE_TASK, {
        "title": "Submit CS3210 lab", "due_at": "2026-09-18T15:59:00+00:00",
        "due_hint": "", "remind_at": "2026-09-18T01:00:00+00:00", "chat_id": "100",
    })
    reviews = StewardReviewInboxApplication(actions, OrganizationProposalRepository(database), SourceRepository(database))

    card = reviews.handle_command(make_event(text=f"/review action {proposal.id}"))

    assert isinstance(card, PresentedReply)
    assert "Due: 18 Sep 2026 · 3:59 pm (UTC+00:00)" in card.text
    assert "Telegram reminder: 18 Sep 2026 · 1:00 am (UTC+00:00)" in card.text


def test_telegram_source_reference_reopens_the_last_explicitly_opened_source_after_restart(tmp_path: Path) -> None:
    database = tmp_path / "steward.db"
    initialize_database(database)
    now = datetime(2026, 9, 10, tzinfo=UTC)
    source_path = tmp_path / "parallel-computing.md"
    source_path.write_text("# OpenMP", encoding="utf-8")
    sources = SourceRepository(database)
    source = sources.add(Source(None, source_path, "a" * 64, SourceType.MARKDOWN, 8, now, now, now))
    fragments = SourceFragmentRepository(database)
    contexts = ReviewContextRepository(database)
    activity = ActivityService(database)
    workspaces = WorkspaceRepository(database)

    def read_application() -> StewardReadApplication:
        return StewardReadApplication(
            sources, fragments, LexicalSearchService(sources, fragments), workspaces,
            activity, tmp_path / "inbox", contexts=contexts,
        )

    class QuestionMustNotRun:
        def invoke(self, _input, _config=None):
            raise AssertionError("an exact source-reference request must not enter retrieval")

    first = StewardEventApplication(
        StewardQuestionApplication(QuestionMustNotRun()),
        StewardCaptureApplication(type("Capture", (), {})()),
        read_application=read_application(),
    )
    opened = first.handle(make_event(text=f"/source {source.id}"))

    assert isinstance(opened, PresentedReply)
    assert opened.title == "parallel-computing.md"
    assert opened.reference == ("source", source.id)
    context = contexts.get("telegram", "100")
    assert context is not None and context.kind == "source"

    restarted = StewardEventApplication(
        StewardQuestionApplication(QuestionMustNotRun()),
        StewardCaptureApplication(type("Capture", (), {})()),
        read_application=read_application(),
    )
    reopened = restarted.handle(make_event(text="open the last source"))

    assert isinstance(reopened, PresentedReply)
    assert reopened.title == "parallel-computing.md"
    empty_content = restarted.handle(make_event(text="give me the content"))
    assert isinstance(empty_content, PresentedReply)
    assert empty_content.reference == ("source", source.id)
    assert "does not establish that the document is empty" in empty_content.text
    assert empty_content.actions[0].command == f"/propose_reextract {source.id}"
    assert fragments.list_for_source(source.id) == ()
    from dataclasses import replace
    for suffix, expected_hint in (
        (".pdf", "Poppler"),
        (".png", "Tesseract"),
        (".docx", "Office Open XML"),
        (".html", "JavaScript"),
        (".eml", "attachments"),
        (".txt", "UTF-8"),
    ):
        sources.update(replace(source, path=source.path.with_suffix(suffix)))
        recovery = restarted.handle(make_event(text="give me the content"))
        assert expected_hint in recovery.text
        assert recovery.actions[0].command == f"/propose_reextract {source.id}"
    sources.update(source)

    empty_memberships = restarted.handle(make_event(text="Which workspace is this in?"))
    assert "not linked to any workspace" in empty_memberships.text
    for index in range(10):
        workspace = workspaces.create(f"Course {index}")
        workspaces.link_source(workspace.id, source.id)
    memberships = restarted.handle(make_event(text="Which workspace is it in?"))
    assert "Course 0" in memberships.text and "Page 1 of 2" in memberships.text
    assert "Course 9" not in memberships.text
    following = next(action.command for action in memberships.actions if action.label == "Next")
    last_memberships = restarted.handle(make_event(text=following))
    assert "Course 9" in last_memberships.text
    assert any(action.command == "/workspace 10" for action in last_memberships.actions)
    assert "physical location" in last_memberships.text

    fragments.replace_for_source(ExtractionResult(source.id, (
        SourceFragment(None, source.id, "OpenMP", 0, "Parallel loops", "page 1"),
        SourceFragment(None, source.id, "Scheduling", 1, "Static scheduling", "page 2"),
    )))
    content = restarted.handle(make_event(text="give me the content"))
    assert isinstance(content, PresentedReply)
    assert content.reference == ("source", source.id)
    assert "Parallel loops" in content.text and "page 1" in content.text
    assert "Static scheduling" not in content.text
    next_action = next(action for action in content.actions if action.label == "Next")
    second = restarted.handle(make_event(text=next_action.command))
    assert "Static scheduling" in second.text and "page 2" in second.text
    assert "Parallel loops" not in second.text
    assert "between 1 and 2" in restarted.handle(make_event(text=f"/source_content {source.id} 3"))
    contexts.clear("telegram", "100")
    assert "Open a source" in restarted.handle(make_event(text="Which workspace is this in?"))
    assert "Open a source" in restarted.handle(make_event(text="give me the content"))

    class SummaryModel:
        def __init__(self):
            self.inputs = []
            self.answer = "Parallel loops use scheduling. [F1]"

        def generate(self, *, instructions, input_text):
            self.inputs.append(input_text)
            return self.answer

    model = SummaryModel()
    privacy = PrivacyService(database)
    reader = StewardReadApplication(
        sources, fragments, LexicalSearchService(sources, fragments), workspaces,
        activity, tmp_path / "inbox", contexts=contexts,
        source_model=model, source_model_allowed=privacy.permits_external_model,
    )
    reader.handle_command(make_event(text=f"/source {source.id}"))
    summary = reader.resolve_source_reference(make_event(text="summarize it"))
    assert summary.reference == ("source", source.id)
    assert "Generated summary of 2 extracted sections" in summary.text
    assert "Parallel loops" in model.inputs[0] and "Static scheduling" in model.inputs[0]
    assert str(tmp_path) not in model.inputs[0]
    prompt = reader.handle_command(make_event(text=f"/ask_source {source.id}"))
    assert prompt.title == "Ask about this source"
    restarted_reader = StewardReadApplication(
        sources, fragments, LexicalSearchService(sources, fragments), workspaces,
        activity, tmp_path / "inbox", contexts=ReviewContextRepository(database),
        source_model=model, source_model_allowed=privacy.permits_external_model,
    )
    answer = restarted_reader.resolve_source_reference(make_event(text="What scheduling is mentioned?"))
    assert answer.title == "Answer: parallel-computing.md"
    assert "Question: What scheduling is mentioned?" in model.inputs[-1]
    assert contexts.get("telegram", "100").kind == "source"
    for invalid_answer in ("Unsupported statement [F99999]", "No citation at all"):
        model.answer = invalid_answer
        unverified = reader.resolve_source_reference(make_event(text="summarize it"))
        assert unverified.title == "Summary needs verification"
        assert invalid_answer not in unverified.text
    calls_before_denial = len(model.inputs)
    privacy.set_rule(source.id, PrivacyRule.NO_MODEL)
    assert "privacy rule" in reader.resolve_source_reference(make_event(text="summarize it"))
    assert len(model.inputs) == calls_before_denial


def test_telegram_workspace_card_and_reference_survive_restart(tmp_path: Path) -> None:
    database = tmp_path / "steward.db"; initialize_database(database)
    contexts = ReviewContextRepository(database)
    sources = SourceRepository(database); fragments = SourceFragmentRepository(database)
    activity = ActivityService(database); workspaces = WorkspaceRepository(database)
    workspace = WorkspaceService(workspaces, activity).create("CS3210")

    def reads() -> StewardReadApplication:
        return StewardReadApplication(
            sources, fragments, LexicalSearchService(sources, fragments), workspaces,
            activity, tmp_path / "inbox", contexts=contexts,
        )

    class QuestionMustNotRun:
        def invoke(self, _input, _config=None):
            raise AssertionError("an exact workspace-reference request must not enter retrieval")

    first = StewardEventApplication(
        StewardQuestionApplication(QuestionMustNotRun()),
        StewardCaptureApplication(type("Capture", (), {})()), read_application=reads(),
    )
    listing = first.handle(make_event(text="/workspaces"))
    assert isinstance(listing, PresentedReply)
    assert listing.actions[0].command == f"/workspace {workspace.id}"
    detail = first.handle(make_event(text=f"/workspace {workspace.id}"))
    assert isinstance(detail, PresentedReply)
    assert detail.title == "CS3210"
    assert detail.reference == ("workspace", workspace.id)

    restarted = StewardEventApplication(
        StewardQuestionApplication(QuestionMustNotRun()),
        StewardCaptureApplication(type("Capture", (), {})()), read_application=reads(),
    )
    reopened = restarted.handle(make_event(text="show that workspace"))
    assert isinstance(reopened, PresentedReply)
    assert reopened.title == "CS3210"


def test_active_review_accepts_a_clear_text_confirmation_for_the_exact_action(tmp_path: Path) -> None:
    """A plain-language reply uses the durable card context, never an inferred ID."""

    database = tmp_path / "steward.db"
    initialize_database(database)
    sources = SourceRepository(database)
    actions = ActionProposalRepository(database)
    proposal = actions.add(ActionProposalService.CREATE_WORKSPACE, {"name": "CS3210 Revision"})
    organizations = OrganizationProposalRepository(database)
    contexts = ReviewContextRepository(database)
    activity = ActivityService(database)
    workspaces = WorkspaceRepository(database)
    reviews = StewardReviewInboxApplication(
        actions, organizations, sources, contexts=contexts
    )
    application = StewardEventApplication(
        StewardQuestionApplication(FakeGraph()),
        StewardCaptureApplication(type("Capture", (), {})()),
        review_inbox_application=reviews,
        action_proposal_application=StewardActionProposalApplication(
            actions,
            ActionProposalService(actions, workspaces, activity),
            activity_service=activity,
            workspace_repository=workspaces,
            source_repository=sources,
        ),
    )

    card = application.handle(make_event(text=f"/review action {proposal.id}"))

    assert isinstance(card, PresentedReply)
    assert card.title == "Create workspace CS3210 Revision"
    accepted = application.handle(make_event(text="yes"))
    assert isinstance(accepted, PresentedReply)
    assert accepted.title == "Workspace created"
    assert actions.get(proposal.id or 0).status == "accepted"
    assert [workspace.name for workspace in workspaces.list_all()] == ["CS3210 Revision"]
    assert contexts.get("telegram", "100") is None


def test_active_review_does_not_confirm_a_stale_action(tmp_path: Path) -> None:
    database = tmp_path / "steward.db"
    initialize_database(database)
    actions = ActionProposalRepository(database)
    proposal = actions.add(ActionProposalService.CREATE_WORKSPACE, {"name": "Stale"})
    actions.set_status(proposal.id or 0, "rejected")
    contexts = ReviewContextRepository(database)
    reviews = StewardReviewInboxApplication(
        actions,
        OrganizationProposalRepository(database),
        SourceRepository(database),
        contexts=contexts,
    )
    event = make_event(text="yes")
    contexts.set(event.platform, event.chat_id, "action", proposal.id or 0)

    assert reviews.contextual_confirmation_command(event) is None
    assert actions.get(proposal.id or 0).status == "rejected"


def test_review_followup_does_not_consume_a_non_review_object_reference(tmp_path: Path) -> None:
    database = tmp_path / "steward.db"
    initialize_database(database)
    contexts = ReviewContextRepository(database)
    contexts.set("telegram", "100", "calendar", "event-1")
    reviews = StewardReviewInboxApplication(
        ActionProposalRepository(database),
        OrganizationProposalRepository(database),
        SourceRepository(database),
        contexts=contexts,
    )

    assert reviews.handle_followup(make_event(text="what is this?")) is None


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


def test_record_detail_shows_current_fields_and_valid_fragment_provenance(tmp_path: Path) -> None:
    database = tmp_path / "steward.db"
    initialize_database(database)
    now = datetime(2026, 9, 10, tzinfo=UTC)
    sources = SourceRepository(database)
    source = sources.add(
        Source(None, tmp_path / "flight.md", "a" * 64, SourceType.MARKDOWN, 0, now, now, now)
    )
    fragments = SourceFragmentRepository(database)
    fragment = fragments.replace_for_source(ExtractionResult(
        source.id or 0,
        (SourceFragment(None, source.id or 0, None, 0, "Flight SQ638\nArrival: Tokyo", "entire file"),),
    ))[0]
    records = RecordService(database)
    record = records.create_from_proposal(records.propose_travel_record(
        source.id or 0, [(fragment.id or 0, fragment.text)]
    ))
    application = StewardRecordApplication(
        records, fragments, ActionProposalRepository(database), ActivityService(database)
    )

    listing = application.handle_command(make_event(text="/records"))
    detail = application.handle_command(make_event(text=f"/record travel {record.id}"))

    assert isinstance(listing, PresentedReply)
    assert listing.title == "Saved records"
    assert listing.actions[0].command == f"/record travel {record.id}"
    assert isinstance(detail, PresentedReply)
    assert "flight: SQ638 (source fragment 1)" in detail.text
    assert "arrival: Tokyo (source fragment 1)" in detail.text
    assert detail.actions[0].command == f"/source {source.id}"
    assert detail.reference == ("record:travel", record.id)

    records.correct_travel_field(record.id or 0, "arrival", "Osaka")
    corrected = application.handle_command(make_event(text=f"/record travel {record.id}"))

    assert isinstance(corrected, PresentedReply)
    assert "arrival: Osaka (not source-evidenced)" in corrected.text


def test_telegram_record_reference_reopens_the_last_explicitly_opened_flight_after_restart(tmp_path: Path) -> None:
    database = tmp_path / "steward.db"
    initialize_database(database)
    now = datetime(2026, 9, 10, tzinfo=UTC)
    sources = SourceRepository(database)
    source = sources.add(Source(None, tmp_path / "flight.md", "a" * 64, SourceType.MARKDOWN, 0, now, now, now))
    fragments = SourceFragmentRepository(database)
    fragment = fragments.replace_for_source(ExtractionResult(
        source.id or 0,
        (SourceFragment(None, source.id or 0, None, 0, "Flight SQ638\nArrival: Tokyo", "entire file"),),
    ))[0]
    records = RecordService(database)
    record = records.create_from_proposal(records.propose_travel_record(
        source.id or 0, [(fragment.id or 0, fragment.text)]
    ))
    contexts = ReviewContextRepository(database)
    activity = ActivityService(database)

    def record_application() -> StewardRecordApplication:
        return StewardRecordApplication(
            records, fragments, ActionProposalRepository(database), activity, contexts=contexts
        )

    class QuestionMustNotRun:
        def invoke(self, _input, _config=None):
            raise AssertionError("an exact record-reference request must not enter retrieval")

    first = StewardEventApplication(
        StewardQuestionApplication(QuestionMustNotRun()),
        StewardCaptureApplication(type("Capture", (), {})()),
        record_application=record_application(),
    )
    opened = first.handle(make_event(text=f"/record travel {record.id}"))
    assert isinstance(opened, PresentedReply)
    assert opened.title == f"Travel record {record.id}"

    restarted = StewardEventApplication(
        StewardQuestionApplication(QuestionMustNotRun()),
        StewardCaptureApplication(type("Capture", (), {})()),
        record_application=record_application(),
    )
    reopened = restarted.handle(make_event(text="show that flight"))

    assert isinstance(reopened, PresentedReply)
    assert reopened.title == f"Travel record {record.id}"
    assert "flight: SQ638" in reopened.text

    provenance = restarted.handle(make_event(text="what source is this from?"))

    assert isinstance(provenance, PresentedReply)
    assert provenance.title == "Record provenance"
    assert provenance.actions[0].command == f"/source {source.id}"
    assert provenance.reference == ("record:travel", record.id)


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
        CalendarEventProposalService(action_repository, records, activity),
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

    assert isinstance(accepted, PresentedReply)
    assert accepted.title == "Travel record saved"
    assert "Calendar event still needs its own review" in accepted.text
    assert accepted.actions[0].command == "/calendar_travel 1"
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
    assert preview.actions[-1].command == f"/source {source.id}"
    reopened = application.handle(make_event(text="/knowledge_proposal 1"))
    assert isinstance(reopened, PresentedReply)
    assert reopened.actions[0].command == "/review_enrichment 1 accepted"
    accepted = application.handle(make_event(text="/review_enrichment 1 accepted"))
    assert accepted == "Knowledge enrichment proposal 1 accepted."

    import sqlite3
    with sqlite3.connect(database_path) as connection:
        connection.execute("UPDATE claims SET text = 'A TLB may cache address translations.' WHERE id = 1")
    new_preview = application.handle(make_event(text="/propose_enrichment 1 1"))
    with sqlite3.connect(database_path) as connection:
        connection.execute("UPDATE claims SET text = 'A TLB sometimes caches translations.' WHERE id = 1")
    recovery = application.handle(make_event(text=new_preview.actions[0].command))
    assert recovery.title == "Knowledge review needs updating"
    assert "claim 1 and fragment 1" in recovery.text
    proposals = KnowledgeEnrichmentProposalRepository(database_path)
    assert proposals.get(2).status == "pending"
    refreshed = application.handle(make_event(text=recovery.actions[0].command))
    assert "sometimes" in refreshed.text
    assert proposals.get(3).status == "pending"
    assert proposals.get(2).status == "pending"
    saved = application.handle(make_event(text=recovery.actions[1].command))
    assert "may cache" in saved.text and "sometimes" not in saved.text
    assert application.handle(make_event(text=refreshed.actions[0].command)) == "Knowledge enrichment proposal 3 accepted."
    application.handle(make_event(text=recovery.actions[2].command))
    assert proposals.get(2).status == "rejected"


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
    recorded = application.handle(make_event(text="/review_enrichment 1 accepted"))
    assert isinstance(recorded, PresentedReply)
    assert recorded.title == "Knowledge conflict recorded"
    assert [action.label for action in recorded.actions] == [
        "Keep claim", "Mark disputed", "Needs revision", "Open source",
    ]
    assert recorded.actions[-1].command == f"/source {source.id}"
    assert knowledge.get_claim(claim.id or 0) == claim
    concept_card = application.handle(make_event(text="/knowledge TLB"))
    assert isinstance(concept_card, PresentedReply)
    assert "contradict" in concept_card.text and "do not establish truth" in concept_card.text
    assert "Conflict status: unresolved" in concept_card.text
    resolved = application.handle(make_event(text=recorded.actions[1].command))
    assert isinstance(resolved, PresentedReply)
    assert resolved.title == "Conflict resolved"
    assert "visibly disputed" in resolved.text
    assert knowledge.get_claim(claim.id or 0) == claim
    concept_card = application.handle(make_event(text="/knowledge TLB"))
    assert "Conflict status: unresolved" not in concept_card.text
    assert "Conflict outcomes: disputed" in concept_card.text
    reviews = application.handle(make_event(text=concept_card.actions[0].command))
    assert "CONTRADICT" in reviews.text
    detail = application.handle(make_event(text=reviews.actions[0].command))
    assert "TLBs do not cache translations." in detail.text
    assert "Conflict outcome: disputed" in detail.text
    from dataclasses import replace
    repository = KnowledgeEnrichmentProposalRepository(database)
    candidate = knowledge.compare_evidence(claim, fragment_id=fragment.id, evidence_text=fragment.text)
    for index in range(8):
        item = repository.add(replace(candidate, rationale=f"Additional review {index}"))
        repository.review(item.id, "accepted")
    page = application.handle(make_event(text=concept_card.actions[0].command))
    next_command = next(action.command for action in page.actions if action.label == "Next")
    last = application.handle(make_event(text=next_command))
    assert "Page 2 of 2" in last.text and "Additional review 7" in last.text
    assert "positive page" in application.handle(make_event(text="/knowledge_reviews 1 0"))
    assert "Current evidence version" in last.text
    import sqlite3
    with sqlite3.connect(database) as connection:
        connection.execute("UPDATE sources SET status = 'missing' WHERE id = ?", (source.id,))
    stale_card = application.handle(make_event(text="/knowledge TLB"))
    assert "Current reviewed evidence" not in stale_card.text
    assert "9 historical review(s)" in stale_card.text
    history = application.handle(make_event(text=stale_card.actions[0].command))
    assert "Historical only" in history.text and "CONTRADICT" in history.text
    assert "Current evidence version" not in history.text
    assert repository.get(1).status == "accepted"
    with sqlite3.connect(database) as connection:
        connection.execute("UPDATE sources SET status = 'active' WHERE id = ?", (source.id,))
    restored = application.handle(make_event(text="/knowledge TLB"))
    assert "Current reviewed evidence: contradict" in restored.text


def test_knowledge_revision_prompt_survives_restart_and_requires_separate_approval(
    tmp_path: Path,
) -> None:
    database = tmp_path / "steward.db"; initialize_database(database)
    now = datetime(2026, 9, 13, tzinfo=UTC)
    sources = SourceRepository(database)
    source = sources.add(Source(
        None, tmp_path / "queues.md", "f" * 64, SourceType.MARKDOWN, 0, now, now, now
    ))
    fragments = SourceFragmentRepository(database)
    fragment = fragments.replace_for_source(ExtractionResult(source.id or 0, (
        SourceFragment(None, source.id or 0, "Queues", 0, "Queues do not always buffer jobs.", "line 2"),
    )))[0]
    knowledge = KnowledgeService(database)
    concept = knowledge.create_concept("Queues")
    original = knowledge.create_claim(concept.id or 0, "Queues always buffer jobs.", [fragment.id or 0])
    conflicts = KnowledgeEnrichmentProposalRepository(database)
    conflict = conflicts.add(knowledge.compare_evidence(
        original, fragment_id=fragment.id or 0, evidence_text="Queues do not always buffer jobs."
    ))
    conflicts.review(conflict.id, "accepted")
    actions = ActionProposalRepository(database)
    activity = ActivityService(database)
    contexts = ReviewContextRepository(database)

    def build_application() -> StewardEventApplication:
        return StewardEventApplication(
            StewardQuestionApplication(FakeGraph()),
            StewardCaptureApplication(type("Capture", (), {})()),
            knowledge_application=StewardKnowledgeApplication(
                knowledge, fragments, conflicts, activity, source_repository=sources,
                action_proposals=actions, contexts=ReviewContextRepository(database),
            ),
            action_proposal_application=StewardActionProposalApplication(
                actions, ActionProposalService(actions, WorkspaceRepository(database), activity),
                activity_service=activity, knowledge_service=knowledge,
            ),
        )

    first = build_application()
    prompt = first.handle(make_event(text=f"/resolve_knowledge_conflict {conflict.id} needs_revision"))

    assert isinstance(prompt, PresentedReply)
    assert "next message" in prompt.text
    assert contexts.get("telegram", "100").kind == "knowledge_claim_revision"
    assert knowledge.list_claims(concept.id or 0) == (original,)

    restarted = build_application()
    draft = restarted.handle(make_event(
        text="Queues may buffer jobs depending on their implementation."
    ))

    assert isinstance(draft, PresentedReply)
    assert draft.title == "Claim revision pending"
    assert "Proposed replacement" in draft.text
    assert knowledge.list_claims(concept.id or 0) == (original,)
    assert contexts.get("telegram", "100") is None

    accepted = restarted.handle(make_event(text=draft.actions[0].command))

    assert isinstance(accepted, PresentedReply)
    assert accepted.title == "Knowledge claim revised"
    claims = knowledge.list_claims(concept.id or 0)
    assert len(claims) == 2 and claims[0] == original
    concept_card = restarted.handle(make_event(text=f"/concept {concept.id}"))
    assert "superseded by claim 2" in concept_card.text
    assert "reviewed revision of claim 1" in concept_card.text


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

    assert isinstance(response, PresentedReply)
    assert response.title == "Authorized source roots"
    assert response.text == "1: School (available)"
    assert response.actions[0].command == "/root 1"
    detail = application.handle(make_event(text="/root 1"))
    assert isinstance(detail, PresentedReply)
    assert detail.title == "School"
    assert "Root paths and changes remain local-only." in detail.text
    roots.set_enabled("School", False)
    disabled = application.handle(make_event(text="/roots"))
    assert isinstance(disabled, PresentedReply)
    assert disabled.text == "1: School (disabled)"

    roots.set_enabled("School", True)
    root_path.rmdir()
    missing = application.handle(make_event(text="/roots"))
    assert isinstance(missing, PresentedReply)
    assert "School (missing)" in missing.text
    assert str(root_path) not in missing.text


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


def test_telegram_privacy_change_is_reviewed_before_it_changes_model_access(tmp_path: Path) -> None:
    database_path = tmp_path / "steward.db"; initialize_database(database_path)
    now = datetime(2026, 9, 9, tzinfo=UTC)
    path = tmp_path / "note.md"; path.write_text("note", encoding="utf-8")
    sources = SourceRepository(database_path)
    source = sources.add(Source(None, path, "b" * 64, SourceType.MARKDOWN, 4, now, now, now))
    activity = ActivityService(database_path)
    proposals = ActionProposalRepository(database_path)
    privacy = PrivacyService(database_path)
    application = StewardEventApplication(
        StewardQuestionApplication(FakeGraph()), StewardCaptureApplication(type("Capture", (), {})()),
        privacy_application=StewardPrivacyApplication(privacy, sources, activity, proposals),
    )

    proposed = application.handle(make_event(text=f"/set_privacy {source.id} no_model"))

    assert isinstance(proposed, PresentedReply)
    assert proposed.title == "Review privacy change"
    assert privacy.rule_for(source.id or 0) is PrivacyRule.EXTERNAL_ALLOWED
    assert proposals.get(1).status == "pending"

    conflicting = application.handle(make_event(text=f"/set_privacy {source.id} local_model_only"))
    assert isinstance(conflicting, PresentedReply)
    assert conflicting.title == "Privacy change pending"
    assert len(proposals.list_all()) == 1

    accepted = application.handle(make_event(text="/approve_action 1"))

    assert isinstance(accepted, PresentedReply)
    assert accepted.title == "Privacy rule applied"
    assert privacy.rule_for(source.id or 0) is PrivacyRule.NO_MODEL
    assert proposals.get(1).status == "accepted"
    repeated = application.handle(make_event(text="/approve_action 1"))
    assert repeated == "Privacy proposal 1 was already accepted."


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

    assert isinstance(found, PresentedReply)
    assert found.title == "Calendar events"
    assert "Flight" in found.text
    assert found.actions[0].command == "/calendar_get event-1"
    assert isinstance(detail, PresentedReply)
    assert detail.title == "Flight"
    assert "Calendar ID: event-1" in detail.text
    assert detail.reference == ("calendar", "event-1")


def test_telegram_calendar_failure_does_not_disclose_local_diagnostics() -> None:
    def unavailable() -> CalendarService:
        raise OSError("C:/private/google-calendar-token.json is unreadable")

    response = StewardCalendarApplication(unavailable).handle_command(
        make_event(text="/calendar_search Tokyo")
    )

    assert response.title == "Calendar unavailable"
    assert "C:/private" not in response.text
    assert response.actions[0].command == "/calendar_search Tokyo"


def test_telegram_calendar_default_limits_to_upcoming_but_named_search_keeps_history() -> None:
    calls = []

    class Reader:
        def search(self, query, **kwargs):
            calls.append((query, kwargs))
            return ()

    application = StewardCalendarApplication(lambda: Reader())
    before = datetime.now(UTC)
    application.handle_command(make_event(text="/calendar_search"))
    application.handle_command(make_event(text="/calendar_search dentist"))
    assert before <= calls[0][1]["time_min"] <= datetime.now(UTC)
    assert calls[1][0] == "dentist" and calls[1][1]["time_min"] is None


def test_empty_calendar_search_is_an_actionable_card() -> None:
    class Reader:
        def search(self, _query, **_kwargs):
            return ()

    response = StewardCalendarApplication(lambda: Reader()).handle_command(
        make_event(text="/calendar_search missing")
    )

    assert isinstance(response, PresentedReply)
    assert response.title == "No Calendar matches"
    assert [action.command for action in response.actions] == ["/calendar_search", "/home"]


def test_telegram_reopens_the_last_calendar_event_after_restart(tmp_path: Path) -> None:
    class Events:
        def get(self, **kwargs):
            assert kwargs["eventId"] == "event-opaque-1"
            return type("Request", (), {"execute": lambda self: {
                "id": "event-opaque-1", "summary": "Flight", "start": {"date": "2026-10-01"},
                "end": {"date": "2026-10-02"},
            }})()

    class Client:
        def events(self):
            return Events()

    database = tmp_path / "steward.db"
    initialize_database(database)
    graph = FakeGraph()
    calendar_factory = lambda: CalendarService(Client())
    first = StewardEventApplication(
        StewardQuestionApplication(graph), StewardCaptureApplication(type("Capture", (), {})()),
        calendar_application=StewardCalendarApplication(
            calendar_factory, contexts=ReviewContextRepository(database)
        ),
    )

    opened = first.handle(make_event(text="/calendar_get event-opaque-1"))
    restarted = StewardEventApplication(
        StewardQuestionApplication(graph), StewardCaptureApplication(type("Capture", (), {})()),
        calendar_application=StewardCalendarApplication(
            calendar_factory, contexts=ReviewContextRepository(database)
        ),
    )
    reopened = restarted.handle(make_event(text="show that event"))
    natural_followup = restarted.handle(make_event(text="when is it?"))

    assert isinstance(opened, PresentedReply)
    assert opened.reference == ("calendar", "event-opaque-1")
    assert isinstance(reopened, PresentedReply)
    assert reopened.title == "Flight"
    assert "Calendar ID: event-opaque-1" in reopened.text
    assert isinstance(natural_followup, PresentedReply)
    assert natural_followup.title == "Flight"
    assert graph.inputs == []


def test_calendar_event_can_navigate_to_an_explicitly_linked_task(tmp_path: Path) -> None:
    database = tmp_path / "steward.db"; initialize_database(database)
    tasks = TaskService(database)
    task = tasks.create("Submit CS3210 lab", due_at=datetime(2026, 10, 1, 9, tzinfo=UTC))
    links = CalendarLinkRepository(database)
    assert links.link_task_event(f"task:{task.id}", task.id or 0, "event-opaque-1") is True
    contexts = ReviewContextRepository(database)
    contexts.set("telegram", "100", "calendar", "event-opaque-1")
    application = StewardCalendarApplication(
        None, contexts=contexts, calendar_links=links, tasks=tasks
    )

    linked = application.resolve_calendar_reference(make_event(text="what task is this for?"))

    assert isinstance(linked, PresentedReply)
    assert linked.title == "Linked task"
    assert "Submit CS3210 lab" in linked.text
    assert linked.actions[0].command == f"/task {task.id}"
    assert linked.actions[1].command == "/calendar_get event-opaque-1"

    event_card = application._event_card(
        make_event(text="/calendar_get event-opaque-1"),
        type("Event", (), {
            "id": "event-opaque-1", "summary": "Due: Submit CS3210 lab",
            "start": "2026-10-01T09:00:00+00:00", "end": "2026-10-01T09:15:00+00:00",
            "location": None, "description": None,
        })(),
    )
    assert any(action.command == "/calendar_linked_task" for action in event_card.actions)
    assert any(action.command == "/tasks" for action in event_card.actions)
    button_linked = application.resolve_calendar_reference(make_event(text="/calendar_linked_task"))
    assert isinstance(button_linked, PresentedReply)
    assert button_linked.title == "Linked task"

    contexts.set("telegram", "100", "calendar", "unlinked-event")
    unlinked = application.resolve_calendar_reference(make_event(text="show linked task"))

    assert isinstance(unlinked, PresentedReply)
    assert unlinked.title == "No linked task"


def test_telegram_preserves_calendar_reference_for_explicit_retry_after_failure(tmp_path: Path) -> None:
    database = tmp_path / "steward.db"
    initialize_database(database)
    contexts = ReviewContextRepository(database)
    contexts.set("telegram", "100", "calendar", "deleted-event")

    def unavailable() -> CalendarService:
        raise OSError("local authorization is unavailable")

    response = StewardCalendarApplication(unavailable, contexts=contexts).resolve_calendar_reference(
        make_event(text="show that event")
    )

    assert response.title == "Calendar unavailable"
    assert contexts.get("telegram", "100").identifier == "deleted-event"
    assert response.actions[0].command == "/calendar_get deleted-event"
    calls = []
    class RecoveredCalendar:
        def get_event(self, identifier):
            calls.append(identifier)
            return type("Event", (), {"id": identifier, "summary": "Updated appointment",
                "location": "Clinic, level 2", "description": "Bring appointment card. " + "x" * 2100,
                "start": "2026-10-01", "end": "2026-10-02"})()
    restarted = StewardCalendarApplication(lambda: RecoveredCalendar(), contexts=ReviewContextRepository(database))
    recovered = restarted.handle_command(make_event(text=response.actions[0].command))
    assert recovered.title == "Updated appointment"
    assert "Location: Clinic, level 2" in recovered.text
    assert "Bring appointment card" in recovered.text
    assert "Truncated; view full details in Calendar" in recovered.text
    assert "x" * 2001 not in recovered.text
    assert calls == ["deleted-event"]
    assert recovered.actions[0].command == "/calendar_get deleted-event"


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
    assert "compare OpenMP scheduling" in preview.title
    assert tasks.list_open() == ()
    accepted = application.handle(make_event(text="/approve_action 1"))
    assert isinstance(accepted, PresentedReply)
    assert accepted.title == "Task saved"
    assert accepted.reference == ("task", 1)
    assert "compare OpenMP scheduling" in accepted.text
    assert tasks.list_open()[0].due_hint == "before Tuesday"
    assert application.handle(make_event(text="/complete_task 1")) == "Task 1 completed: compare OpenMP scheduling."
    assert tasks.list_open() == ()


def test_telegram_task_card_and_reference_survive_restart(tmp_path: Path) -> None:
    database = tmp_path / "steward.db"; initialize_database(database)
    tasks = TaskService(database)
    task = tasks.create("compare OpenMP scheduling", "before Tuesday")
    contexts = ReviewContextRepository(database)
    activity = ActivityService(database)

    def task_application() -> StewardTaskApplication:
        return StewardTaskApplication(tasks, ActionProposalRepository(database), activity, contexts=contexts)

    class QuestionMustNotRun:
        def invoke(self, _input, _config=None):
            raise AssertionError("an exact task-reference request must not enter retrieval")

    first = StewardEventApplication(
        StewardQuestionApplication(QuestionMustNotRun()),
        StewardCaptureApplication(type("Capture", (), {})()), task_application=task_application(),
    )
    listing = first.handle(make_event(text="/tasks"))
    assert isinstance(listing, PresentedReply)
    assert listing.actions[0].command == f"/task {task.id}"
    detail = first.handle(make_event(text=f"/task {task.id}"))
    assert isinstance(detail, PresentedReply)
    assert detail.actions[0].command == f"/complete_task {task.id}"
    assert detail.reference == ("task", task.id)

    restarted = StewardEventApplication(
        StewardQuestionApplication(QuestionMustNotRun()),
        StewardCaptureApplication(type("Capture", (), {})()), task_application=task_application(),
    )
    reopened = restarted.handle(make_event(text="show that task"))
    assert isinstance(reopened, PresentedReply)
    assert reopened.title == f"Task {task.id}"
    assert "compare OpenMP scheduling" in reopened.text


def test_opened_task_allows_a_narrow_natural_completion_followup_after_restart(tmp_path: Path) -> None:
    database = tmp_path / "steward.db"; initialize_database(database)
    tasks = TaskService(database); activity = ActivityService(database)
    task = tasks.create("Compare OpenMP scheduling")
    contexts = ReviewContextRepository(database)
    first = StewardTaskApplication(
        tasks, ActionProposalRepository(database), activity, contexts=contexts
    )

    opened = first.handle_command(make_event(text=f"/task {task.id}"))
    restarted = StewardTaskApplication(
        TaskService(database), ActionProposalRepository(database), ActivityService(database),
        contexts=ReviewContextRepository(database),
    )
    completed = restarted.resolve_task_reference(make_event(text="mark that task complete"))

    assert isinstance(opened, PresentedReply)
    assert isinstance(completed, PresentedReply)
    assert completed.title == "Task completed"
    assert "No Calendar event was changed" in completed.text
    assert TaskService(database).get(task.id or 0).status == "completed"
    assert [event.event_type for event in ActivityService(database).list_recent()].count("task_completed") == 1

    repeated = restarted.resolve_task_reference(make_event(text="complete this task"))
    assert isinstance(repeated, PresentedReply)
    assert repeated.title == "Task already completed"
    assert [event.event_type for event in ActivityService(database).list_recent()].count("task_completed") == 1


def test_task_detail_distinguishes_an_optional_calendar_marker_from_the_task(tmp_path: Path) -> None:
    database = tmp_path / "steward.db"; initialize_database(database)
    tasks = TaskService(database)
    task = tasks.create(
        "submit CS3210 lab", due_at=datetime(2026, 9, 18, 15, 59, tzinfo=UTC)
    )
    links = CalendarLinkRepository(database)
    application = StewardTaskApplication(
        tasks, ActionProposalRepository(database), ActivityService(database),
        calendar_links=links,
    )

    unlinked = application.handle_command(make_event(text=f"/task {task.id}"))

    assert isinstance(unlinked, PresentedReply)
    assert "Calendar: no linked event" in unlinked.text
    assert unlinked.actions[0].command == f"/calendar_task {task.id}"
    assert tasks.get(task.id or 0).status == "open"

    assert links.link_task_event(f"task:{task.id}", task.id or 0, "event-opaque-7") is True
    linked = application.handle_command(make_event(text=f"/task {task.id}"))

    assert isinstance(linked, PresentedReply)
    assert "Calendar: linked deadline marker" in linked.text
    assert linked.actions[0].command == "/calendar_get event-opaque-7"
    assert all(action.command != f"/calendar_task {task.id}" for action in linked.actions)
    assert tasks.get(task.id or 0).status == "open"


def test_deterministic_natural_task_phrase_creates_the_same_reviewable_proposal(tmp_path: Path) -> None:
    database = tmp_path / "steward.db"; initialize_database(database)
    activity = ActivityService(database); proposals = ActionProposalRepository(database); tasks = TaskService(database)
    application = StewardEventApplication(
        StewardQuestionApplication(FakeGraph()), StewardCaptureApplication(type("Capture", (), {})()),
        task_application=StewardTaskApplication(tasks, proposals, activity),
    )

    response = application.handle(make_event(text="remind me to compare OpenMP scheduling before Tuesday"))

    assert isinstance(response, PresentedReply)
    assert response.title == "Save task: compare OpenMP scheduling"
    assert "No task has been saved yet." in response.text
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


def test_time_bound_personal_commitment_creates_a_reviewable_task_proposal(tmp_path: Path) -> None:
    database = tmp_path / "steward.db"; initialize_database(database)
    activity = ActivityService(database); proposals = ActionProposalRepository(database); tasks = TaskService(database)
    application = StewardEventApplication(
        StewardQuestionApplication(FakeGraph()), StewardCaptureApplication(type("Capture", (), {})()),
        task_application=StewardTaskApplication(tasks, proposals, activity),
    )

    response = application.handle(make_event(text="I need to submit CS3210 lab by Friday"))

    assert isinstance(response, PresentedReply)
    assert response.title == "Save task: submit CS3210 lab"
    assert "Due cue: by Friday" in response.text
    assert tasks.list_open() == ()


def test_explicit_task_deadline_is_reviewed_and_persisted_with_its_timezone(tmp_path: Path) -> None:
    database = tmp_path / "steward.db"; initialize_database(database)
    activity = ActivityService(database); proposals = ActionProposalRepository(database); tasks = TaskService(database)
    application = StewardEventApplication(
        StewardQuestionApplication(FakeGraph()), StewardCaptureApplication(type("Capture", (), {})()),
        task_application=StewardTaskApplication(tasks, proposals, activity),
        action_proposal_application=StewardActionProposalApplication(
            proposals, ActionProposalService(proposals, WorkspaceRepository(database), activity),
            CalendarEventProposalService(proposals, RecordService(database), activity, tasks),
            activity_service=activity, task_service=tasks,
        ),
    )

    preview = application.handle(
        make_event(text="/propose_task submit CS3210 lab --due-at 2026-09-18T23:59:00+08:00")
    )

    assert isinstance(preview, PresentedReply)
    assert "Due at: 18 Sep 2026 · 3:59 pm (UTC+00:00)" in preview.text
    accepted = application.handle(make_event(text="/approve_action 1"))
    assert isinstance(accepted, PresentedReply)
    assert accepted.title == "Task saved"
    assert accepted.actions[0].command == "/calendar_task 1"
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
    assert "Reminder at: 18 Sep 2026 · 1:00 am (UTC+00:00)" in preview.text
    accepted = application.handle(make_event(text="/approve_action 1"))
    assert isinstance(accepted, PresentedReply)
    assert accepted.title == "Task saved"
    reminder = reminders.reminder_for_task(1)
    assert reminder is not None and reminder.chat_id == "100"
    task_list = application.handle(make_event(text="/tasks"))
    assert isinstance(task_list, PresentedReply)
    assert "reminder 18 Sep 2026 · 1:00 am (UTC+00:00)" in task_list.text


def test_workspace_pages_reach_every_workspace_and_linked_source(tmp_path: Path) -> None:
    database = tmp_path / "steward.db"
    initialize_database(database)
    sources = SourceRepository(database)
    fragments = SourceFragmentRepository(database)
    workspaces = WorkspaceRepository(database)
    contexts = ReviewContextRepository(database)
    for index in range(10):
        workspaces.create(f"Project {index}")
    now = datetime(2026, 9, 10, tzinfo=UTC)
    for index in range(7):
        source = sources.add(Source(None, tmp_path / f"note-{index}.md", "a" * 64, SourceType.MARKDOWN, 0, now, now, now))
        workspaces.link_source(1, source.id)
    reader = StewardReadApplication(sources, fragments, LexicalSearchService(sources, fragments), workspaces, ActivityService(database), tmp_path / "inbox", contexts=contexts)
    first = reader.handle_command(make_event(text="/workspaces"))
    next_command = next(action.command for action in first.actions if action.label == "Next")
    second = reader.handle_command(make_event(text=next_command))
    assert "Project 9" in second.text and "Page 2 of 2" in second.text
    detail = reader.handle_command(make_event(text="/workspace 1"))
    next_command = next(action.command for action in detail.actions if action.label == "Next")
    second_detail = reader.handle_command(make_event(text=next_command))
    assert "note-6.md" in second_detail.text and "Page 2 of 2" in second_detail.text
    assert any(action.command == "/source 7" for action in second_detail.actions)
    assert contexts.get("telegram", "100").identifier == 1
    assert reader.handle_command(make_event(text="/workspace 1 999")).text == second_detail.text
    assert "positive" in reader.handle_command(make_event(text="/workspace 1 0"))
    assert "positive" in reader.handle_command(make_event(text="/workspaces -1"))
    empty = reader.handle_command(make_event(text="/workspace 2"))
    assert any(action.command == "/sources" for action in empty.actions)


def test_knowledge_browser_reaches_all_concepts_without_name_commands(tmp_path: Path) -> None:
    database = tmp_path / "steward.db"
    initialize_database(database)
    knowledge = KnowledgeService(database)
    app = StewardKnowledgeApplication(knowledge, SourceFragmentRepository(database), KnowledgeEnrichmentProposalRepository(database), ActivityService(database))
    empty = app.handle_command(make_event(text="/knowledge"))
    assert "No saved concepts" in empty.text
    for index in range(10):
        knowledge.create_concept(f"Concept {index}")
    first = app.handle_command(make_event(text="/knowledge"))
    next_command = next(action.command for action in first.actions if action.label == "Next")
    last = app.handle_command(make_event(text=next_command))
    assert "Page 2 of 2" in last.text and "Concept 9" in last.text
    detail = app.handle_command(make_event(text=last.actions[1].command))
    assert detail.title == "Concept 9"
    assert any(action.command == "/knowledge" for action in detail.actions)
    assert "positive page" in app.handle_command(make_event(text="/concepts 0"))
    assert app.handle_command(make_event(text="/concepts 999")).text == last.text


def test_task_browser_reaches_later_pages_and_completed_history(tmp_path: Path) -> None:
    database = tmp_path / "steward.db"
    initialize_database(database)
    tasks = TaskService(database)
    for index in range(10):
        tasks.create(f"Task {index}")
    app = StewardTaskApplication(tasks, ActionProposalRepository(database), ActivityService(database))
    first = app.handle_command(make_event(text="/tasks"))
    command = next(action.command for action in first.actions if action.label == "Next")
    last = app.handle_command(make_event(text=command))
    assert "Page 2 of 2" in last.text and "Task 9" in last.text
    assert last.actions[1].command == "/task 10"
    for task in tasks.list_open():
        tasks.complete(task.id)
    empty = app.handle_command(make_event(text="/tasks"))
    completed_command = next(action.command for action in empty.actions if action.label == "Completed")
    completed = app.handle_command(make_event(text=completed_command))
    assert completed.title == "Completed tasks"
    assert any(action.label == "Next" for action in completed.actions)
    detail = app.handle_command(make_event(text=completed.actions[0].command))
    assert "completed" in detail.text
    assert all(action.label != "Mark complete" for action in detail.actions)
    assert len(tasks.list_completed()) == 10
    assert "positive page" in app.handle_command(make_event(text="/completed_tasks 0"))


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


def test_record_approval_rejects_changed_evidence_and_legacy_previews(tmp_path: Path) -> None:
    for kind, original, changed in (
        ("travel", "Flight SQ638\nArrival: Tokyo", "Flight SQ638\nArrival: Osaka"),
        ("receipt", "Merchant: Cafe\nTotal: SGD 12.50", "Merchant: Cafe\nTotal: SGD 99.00"),
        ("warranty", "Product: Laptop\nProvider: Shop", "Product: Phone\nProvider: Shop"),
    ):
        database = tmp_path / f"{kind}.db"
        initialize_database(database)
        now = datetime(2026, 9, 10, tzinfo=UTC)
        source = SourceRepository(database).add(Source(None, tmp_path / f"{kind}.md", "a" * 64, SourceType.MARKDOWN, 1, now, now, now))
        fragments = SourceFragmentRepository(database)
        fragments.replace_for_source(ExtractionResult(source.id, (
            SourceFragment(None, source.id, None, 0, original, "lines 1-2"),
        )))
        activity = ActivityService(database)
        proposals = ActionProposalRepository(database)
        records = RecordService(database)
        intake = StewardRecordApplication(records, fragments, proposals, activity)
        intake.handle_command(make_event(text=f"/propose_{kind}_record {source.id}"))
        pending = proposals.get(1)
        assert "snapshot" in pending.payload
        fragments.replace_for_source(ExtractionResult(source.id, (
            SourceFragment(None, source.id, None, 0, changed, "lines 1-2"),
        )))
        review = StewardActionProposalApplication(
            proposals, ActionProposalService(proposals, WorkspaceRepository(database), activity),
            record_service=records, fragment_repository=fragments, activity_service=activity,
        )
        application = StewardEventApplication(
            StewardQuestionApplication(FakeGraph()), StewardCaptureApplication(type("Capture", (), {})()),
            action_proposal_application=review, record_application=intake,
        )
        stale = application.handle(make_event(text="/approve_action 1"))
        assert isinstance(stale, PresentedReply)
        assert "stale" in stale.text
        assert [action.command for action in stale.actions] == [
            f"/propose_{kind}_record {source.id}", f"/source {source.id}", "/reject_action 1",
        ]
        assert proposals.get(1).status == "pending"
        legacy = proposals.add(f"create_{kind}_record", {"source_id": str(source.id)})
        assert "snapshot protection" in application.handle(make_event(text=f"/approve_action {legacy.id}")).text
        assert getattr(records, f"list_{kind}_records")() == []
        fresh = application.handle(make_event(text=stale.actions[0].command))
        assert getattr(records, f"list_{kind}_records")() == []
        approve = next(action.command for action in fresh.actions if action.command.startswith("/approve_action"))
        application.handle(make_event(text=approve))
        assert len(getattr(records, f"list_{kind}_records")()) == 1


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


def test_telegram_research_failure_does_not_disclose_provider_diagnostics(tmp_path: Path) -> None:
    class Provider:
        def research(self, _query: str) -> ResearchBundle:
            raise ResearchProviderError("C:/private/research-provider-token is unavailable")

    database = tmp_path / "steward.db"; initialize_database(database)
    capture = InboxCaptureService(tmp_path / "vault" / "inbox", SourceRepository(database))
    application = StewardResearchApplication(lambda: Provider(), ResearchRetentionService(capture))

    response = application.handle_command(make_event(text="/research TLB shootdowns"))

    assert response == "External research is temporarily unavailable. Please retry later."
    assert "C:/private" not in response


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


def test_telegram_research_retention_card_survives_a_local_restart(tmp_path: Path) -> None:
    database = tmp_path / "steward.db"; initialize_database(database)
    capture = InboxCaptureService(tmp_path / "vault" / "inbox", SourceRepository(database), SourceFragmentRepository(database))

    class Provider:
        def research(self, query: str) -> ResearchBundle:
            return ResearchBundle(query, "Reviewed answer", (ResearchSource("Example", "https://example.com"),), provider="fake")

    first = StewardResearchApplication(
        lambda: Provider(), ResearchRetentionService(capture), EphemeralResearchCardRepository(database)
    )
    preview = first.handle_command(make_event(text="/research TLB"))
    assert isinstance(preview, PresentedReply)
    token_command = preview.actions[0].command

    restarted = StewardResearchApplication(
        lambda: None, ResearchRetentionService(capture), EphemeralResearchCardRepository(database)
    )
    retained = restarted.handle_command(make_event(text=token_command))

    assert "Retained the reviewed external research note" in retained
    assert "Reviewed answer" in SourceRepository(database).list_all()[0].path.read_text(encoding="utf-8")


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
    assert isinstance(saved, PresentedReply)
    assert saved.title == "Curated note saved"
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
    saved = application.handle(make_event(text="/approve_action 1"))
    assert isinstance(saved, PresentedReply)
    assert saved.title == "Curated note saved"
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
    saved = application.handle(make_event(text="/approve_action 1"))
    assert isinstance(saved, PresentedReply)
    assert saved.title == "Curated note saved"
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


def test_telegram_can_edit_a_curated_note_before_it_is_saved(tmp_path: Path) -> None:
    database = tmp_path / "steward.db"; initialize_database(database)
    activity = ActivityService(database); proposals = ActionProposalRepository(database)
    sources = SourceRepository(database)
    capture = InboxCaptureService(tmp_path / "vault" / "inbox", sources, SourceFragmentRepository(database), activity)
    contexts = ReviewContextRepository(database)
    application = StewardEventApplication(
        StewardQuestionApplication(FakeGraph()), StewardCaptureApplication(type("Capture", (), {})()),
        curated_note_application=StewardCuratedNoteApplication(proposals, activity, contexts=contexts),
        action_proposal_application=StewardActionProposalApplication(
            proposals, ActionProposalService(proposals, WorkspaceRepository(database), activity),
            activity_service=activity, capture_service=capture,
        ),
    )

    initial = application.handle(make_event(text="/propose_note # TLB\n\nA TLB caches translations."))
    prompt = application.handle(make_event(text="/curate_edit 1"))
    revised = application.handle(make_event(text="# TLB\n\nA TLB caches recent address translations."))

    assert isinstance(initial, PresentedReply)
    assert any(action.label == "Edit" for action in initial.actions)
    assert isinstance(prompt, PresentedReply)
    assert prompt.title == "Edit curated note"
    assert isinstance(revised, PresentedReply)
    assert "recent address translations" in revised.text
    assert proposals.get(1).status == "rejected"
    assert proposals.get(2).status == "pending"

    saved = application.handle(make_event(text="/approve_action 2"))

    assert isinstance(saved, PresentedReply)
    assert sources.list_all()[0].path.read_text(encoding="utf-8").endswith(
        "A TLB caches recent address translations.\n"
    )


def test_pending_curated_note_edit_survives_an_application_restart(tmp_path: Path) -> None:
    database = tmp_path / "steward.db"; initialize_database(database)
    activity = ActivityService(database); proposals = ActionProposalRepository(database)
    contexts = ReviewContextRepository(database)

    def build() -> StewardEventApplication:
        return StewardEventApplication(
            StewardQuestionApplication(FakeGraph()), StewardCaptureApplication(type("Capture", (), {})()),
            curated_note_application=StewardCuratedNoteApplication(proposals, activity, contexts=contexts),
        )

    first = build()
    first.handle(make_event(text="/propose_note # TLB\n\nInitial draft."))
    prompt = first.handle(make_event(text="/curate_edit 1"))
    restarted = build()
    revised = restarted.handle(make_event(text="# TLB\n\nEdited after restart."))

    assert isinstance(prompt, PresentedReply)
    assert isinstance(revised, PresentedReply)
    assert "Edited after restart" in revised.text
    assert proposals.get(1).status == "rejected"
    assert proposals.get(2).status == "pending"


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
    for index in range(9):
        WorkspaceService(workspaces, activity).create(f"Workspace {index}")
    proposals = ActionProposalRepository(database)
    application = StewardEventApplication(
        StewardQuestionApplication(FakeGraph()), StewardCaptureApplication(type("Capture", (), {})()),
        workspace_link_application=StewardWorkspaceLinkApplication(proposals, workspaces, sources, activity),
        action_proposal_application=StewardActionProposalApplication(
            proposals, ActionProposalService(proposals, workspaces, activity), activity_service=activity,
            workspace_repository=workspaces, source_repository=sources,
        ),
    )

    picker = application.handle(make_event(text="/source_workspaces 1"))
    assert isinstance(picker, PresentedReply)
    assert "note.md" in picker.text and "Page 1 of 2" in picker.text
    assert workspaces.list_source_ids(1) == ()
    later = application.handle(make_event(text="/source_workspaces 1 2"))
    assert isinstance(later, PresentedReply)
    assert "Workspace 8" in later.text
    assert any(action.command == "/propose_link_source 10 1" for action in later.actions)
    assert "positive page" in application.handle(make_event(text="/source_workspaces 1 0"))
    assert "unavailable" in application.handle(make_event(text="/source_workspaces 999"))
    preview = application.handle(make_event(text=picker.actions[0].command))
    assert isinstance(preview, PresentedReply)
    assert "No file will move" in preview.text
    assert "CS3210" in preview.text and "note.md" in preview.text
    assert workspaces.list_source_ids(1) == ()
    assert application.handle(make_event(text="/approve_action 1")) == "Source 1 linked to workspace 1. No file moved."
    assert source_path.is_file()
    linked = application.handle(make_event(text="/source_workspaces 1"))
    assert "already linked" in linked.text
    assert linked.actions[0].command == "/workspace 1"
    stale = application.handle(make_event(text="/propose_link_source 2 1"))
    import sqlite3
    with sqlite3.connect(database) as connection:
        connection.execute("UPDATE sources SET status = 'missing' WHERE id = 1")
    assert "no longer available" in application.handle(make_event(text=stale.actions[0].command))
    assert workspaces.list_source_ids(2) == ()


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

    recovery = application.handle_command(make_event(text=f"/approve_action {proposal.id}"))
    assert recovery.title == "Text refresh incomplete"
    assert "source 99" in recovery.text
    assert recovery.actions[0].command == f"/approve_action {proposal.id}"
    assert proposals.get(proposal.id or 0).status == "pending"
    calls = []
    class RecoveringExtractor:
        def reextract_source(self, source_id):
            calls.append(source_id)
            if len(calls) == 1:
                raise RuntimeError("C:/private/secret-source.pdf token=synthetic-secret")
            return ()
    restarted = StewardActionProposalApplication(
        proposals, ActionProposalService(proposals, WorkspaceRepository(database), activity),
        activity_service=activity, source_repository=sources, source_service=RecoveringExtractor(),
    )
    failure = restarted.handle_command(make_event(text=recovery.actions[0].command))
    assert "synthetic-secret" not in failure.text and "C:/private" not in failure.text
    assert "partially refreshed" in failure.text
    assert proposals.get(proposal.id).status == "pending"
    success = restarted.handle_command(make_event(text=failure.actions[0].command))
    assert "0 fragments" in success
    assert proposals.get(proposal.id).status == "accepted"
    assert calls == [99, 99]


def test_telegram_reextract_failure_explains_the_specific_extractor_without_leaking(
    tmp_path: Path,
) -> None:
    database = tmp_path / "steward.db"; initialize_database(database)
    now = datetime(2026, 9, 13, tzinfo=UTC)
    sources = SourceRepository(database)
    source = sources.add(Source(
        None, tmp_path / "lecture.docx", "d" * 64, SourceType.DOCX, 20, now, now, now
    ))
    proposals = ActionProposalRepository(database)
    proposal = proposals.add(StewardActionProposalApplication.REEXTRACT_SOURCE, {"source_id": str(source.id)})

    class FailingExtractor:
        def reextract_source(self, source_id):
            raise RuntimeError("C:/private/lecture.docx secret parser diagnostic")

    application = StewardActionProposalApplication(
        proposals,
        ActionProposalService(proposals, WorkspaceRepository(database), ActivityService(database)),
        source_repository=sources,
        source_service=FailingExtractor(),
    )

    recovery = application.handle_command(make_event(text=f"/approve_action {proposal.id}"))

    assert isinstance(recovery, PresentedReply)
    assert "Office Open XML" in recovery.text and "renamed legacy `.doc`" in recovery.text
    assert "secret parser diagnostic" not in recovery.text and "C:/private" not in recovery.text
    assert any(action.command == f"/source {source.id}" for action in recovery.actions)
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


def test_telegram_unregister_source_is_reviewed_and_preserves_the_original(tmp_path: Path) -> None:
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
        ExtractionResult(source.id or 0, (SourceFragment(None, source.id or 0, None, 0, original, "lines 1-3"),))
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
        ),
    )

    preview = application.handle(make_event(text=f"/propose_unregister_source {source.id}"))

    assert isinstance(preview, PresentedReply)
    assert "notes.md" in preview.text
    assert str(tmp_path) not in preview.text
    assert "will not be deleted" in preview.text
    assert sources.get_by_id(source.id or 0) is not None
    assert source_path.read_text(encoding="utf-8") == original

    accepted = application.handle(make_event(text="/approve_action 1"))

    assert accepted == "Unregistered source 1 from Steward metadata. Original file unchanged."
    assert sources.get_by_id(source.id or 0) is None
    assert fragments.list_for_source(source.id or 0) == ()
    assert source_path.read_text(encoding="utf-8") == original
    assert proposals.get(1).status == "accepted"
    assert activity.list_recent()[1].event_type is ActivityType.SOURCE_UNREGISTERED


def test_telegram_integration_status_reveals_only_local_readiness(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("STEWARD_GOOGLE_CLIENT_SECRETS", "C:/private/client.json")
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "google-calendar-token.json").write_text("secret token", encoding="utf-8")
    application = StewardEventApplication(
        StewardQuestionApplication(FakeGraph()), StewardCaptureApplication(type("Capture", (), {})()),
        integration_status_application=StewardIntegrationStatusApplication(tmp_path),
    )

    response = application.handle(make_event(text="/integrations"))

    assert isinstance(response, PresentedReply)
    assert response.title == "Integration status"
    assert "OAuth client: configured locally" in response.text
    assert "Calendar: local token present" in response.text
    assert "Drive: needs local browser authorization" in response.text
    assert "secret token" not in response.text and "client.json" not in response.text


def test_telegram_integration_status_reports_expired_token_without_disclosing_it(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("STEWARD_GOOGLE_CLIENT_SECRETS", "C:/private/client.json")
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "google-drive-token.json").write_text(
        '{"token":"secret","expiry":"2020-01-01T00:00:00+00:00","scopes":["https://www.googleapis.com/auth/drive.readonly"]}', encoding="utf-8"
    )
    response = StewardIntegrationStatusApplication(tmp_path).handle_command(make_event(text="/integrations"))

    assert isinstance(response, PresentedReply)
    assert "Drive: local token expired; reauthorize locally" in response.text
    assert "secret" not in response.text and "client.json" not in response.text


def test_telegram_integration_status_flags_insufficient_drive_scope_without_disclosing_scope(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("STEWARD_GOOGLE_CLIENT_SECRETS", "C:/private/client.json")
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "google-drive-token.json").write_text(
        '{"token":"secret","expiry":"2099-01-01T00:00:00+00:00","scopes":["metadata-only"]}',
        encoding="utf-8",
    )

    response = StewardIntegrationStatusApplication(tmp_path).handle_command(make_event(text="/integrations"))

    assert isinstance(response, PresentedReply)
    assert "Drive: local token lacks required access; reauthorize locally" in response.text
    assert "secret" not in response.text and "metadata-only" not in response.text


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
    assert response.actions[0].label == "Open 1"
    assert response.actions[-1].label == "Next"
    assert response.actions[-1].command == "/sources 2"


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


def test_provisional_intake_collects_context_from_an_ordinary_followup(tmp_path: Path) -> None:
    database_path = tmp_path / "steward.db"
    initialize_database(database_path)
    sources = SourceRepository(database_path)
    activity = ActivityService(database_path)
    capture_service = InboxCaptureService(tmp_path / "vault" / "inbox", sources, activity_service=activity)
    provisional = StewardProvisionalIntakeApplication(
        ProvisionalIntakeService(
            tmp_path / ".steward" / "cache" / "intake", ProvisionalIntakeRepository(database_path),
            capture_service, activity, PrivacyService(database_path),
        ),
        contexts=ReviewContextRepository(database_path),
    )
    application = StewardEventApplication(
        StewardQuestionApplication(FakeGraph()), StewardCaptureApplication(capture_service),
        provisional_intake_application=provisional,
    )
    original = tmp_path / "notes.pdf"
    original.write_bytes(b"pdf")
    upload = IncomingEvent(
        "telegram:attachment", "telegram", "100", "12", None,
        datetime(2026, 9, 9, tzinfo=UTC), None, ("notes.pdf",),
    )
    card = application.handle_file(upload, original)

    prompt = application.handle(make_event(text="/intake_context 1"))
    revised = application.handle(make_event(text="This is for my CS3210 OpenMP assignment."))

    assert isinstance(card, PresentedReply)
    assert "Provisional intake" not in card.text
    assert isinstance(prompt, PresentedReply)
    assert prompt.title == "Add context"
    assert isinstance(revised, PresentedReply)
    assert "CS3210 OpenMP assignment" in revised.text
    assert sources.list_all() == []


def test_provisional_intake_context_followup_survives_a_restart(tmp_path: Path) -> None:
    database_path = tmp_path / "steward.db"
    initialize_database(database_path)
    sources = SourceRepository(database_path)
    activity = ActivityService(database_path)
    capture_service = InboxCaptureService(tmp_path / "vault" / "inbox", sources, activity_service=activity)

    def build_application() -> StewardEventApplication:
        provisional = StewardProvisionalIntakeApplication(
            ProvisionalIntakeService(
                tmp_path / ".steward" / "cache" / "intake", ProvisionalIntakeRepository(database_path),
                capture_service, activity, PrivacyService(database_path),
            ),
            contexts=ReviewContextRepository(database_path),
        )
        return StewardEventApplication(
            StewardQuestionApplication(FakeGraph()), StewardCaptureApplication(capture_service),
            provisional_intake_application=provisional,
        )

    original = tmp_path / "notes.pdf"
    original.write_bytes(b"pdf")
    first = build_application()
    first.handle_file(
        IncomingEvent(
            "telegram:attachment", "telegram", "100", "12", None,
            datetime(2026, 9, 9, tzinfo=UTC), None, ("notes.pdf",),
        ),
        original,
    )
    assert isinstance(first.handle(make_event(text="/intake_context 1")), PresentedReply)

    restarted = build_application()
    revised = restarted.handle(make_event(text="This belongs to my CS4226 network notes."))

    assert isinstance(revised, PresentedReply)
    assert "CS4226 network notes" in revised.text
    assert sources.list_all() == []


def test_replying_save_to_a_staged_attachment_accepts_that_exact_intake(tmp_path: Path) -> None:
    database_path = tmp_path / "steward.db"
    initialize_database(database_path)
    sources = SourceRepository(database_path)
    activity = ActivityService(database_path)
    capture_service = InboxCaptureService(tmp_path / "vault" / "inbox", sources, activity_service=activity)
    application = StewardEventApplication(
        StewardQuestionApplication(FakeGraph()),
        StewardCaptureApplication(capture_service),
        provisional_intake_application=StewardProvisionalIntakeApplication(
            ProvisionalIntakeService(
                tmp_path / ".steward" / "cache" / "intake", ProvisionalIntakeRepository(database_path),
                capture_service, activity, PrivacyService(database_path),
            )
        ),
    )
    original = tmp_path / "lecture-notes.md"
    original.write_text("# OpenMP\n", encoding="utf-8")
    upload = IncomingEvent(
        "telegram:attachment", "telegram", "100", "12", None,
        datetime(2026, 9, 9, tzinfo=UTC), None, ("lecture-notes.md",),
    )
    application.handle_file(upload, original)

    response = application.handle(
        IncomingEvent(
            "telegram:save-reply", "telegram", "100", "13", "12",
            datetime(2026, 9, 9, tzinfo=UTC), "/save",
        )
    )

    assert "Saved to Inbox" in response
    assert [source.path.name for source in sources.list_all()] == ["telegram-100-12-lecture-notes.md"]
    intake = ProvisionalIntakeRepository(database_path).get(1)
    assert intake is not None and intake.status == "accepted"


def test_reply_save_never_accepts_an_intake_from_another_chat(tmp_path: Path) -> None:
    database_path = tmp_path / "steward.db"
    initialize_database(database_path)
    sources = SourceRepository(database_path)
    activity = ActivityService(database_path)
    capture_service = InboxCaptureService(tmp_path / "vault" / "inbox", sources, activity_service=activity)
    application = StewardEventApplication(
        StewardQuestionApplication(FakeGraph()),
        StewardCaptureApplication(capture_service),
        provisional_intake_application=StewardProvisionalIntakeApplication(
            ProvisionalIntakeService(
                tmp_path / ".steward" / "cache" / "intake", ProvisionalIntakeRepository(database_path),
                capture_service, activity, PrivacyService(database_path),
            )
        ),
    )
    original = tmp_path / "private.md"
    original.write_text("private", encoding="utf-8")
    application.handle_file(
        IncomingEvent(
            "telegram:attachment", "telegram", "100", "12", None,
            datetime(2026, 9, 9, tzinfo=UTC), None, ("private.md",),
        ),
        original,
    )

    response = application.handle(
        IncomingEvent(
            "telegram:save-reply", "telegram", "200", "13", "12",
            datetime(2026, 9, 9, tzinfo=UTC), "/save",
        )
    )

    assert "Use /save followed by the text" in response
    assert sources.list_all() == []
    intake = ProvisionalIntakeRepository(database_path).get(1)
    assert intake is not None and intake.status == "pending"


def test_short_personal_record_text_is_staged_without_being_saved(tmp_path: Path) -> None:
    database_path = tmp_path / "steward.db"
    initialize_database(database_path)
    sources = SourceRepository(database_path)
    activity = ActivityService(database_path)
    capture_service = InboxCaptureService(tmp_path / "vault" / "inbox", sources, activity_service=activity)
    application = StewardEventApplication(
        StewardQuestionApplication(FakeGraph()), StewardCaptureApplication(capture_service),
        provisional_intake_application=StewardProvisionalIntakeApplication(
            ProvisionalIntakeService(
                tmp_path / ".steward" / "cache" / "intake", ProvisionalIntakeRepository(database_path),
                capture_service, activity, PrivacyService(database_path),
            )
        ),
    )

    response = application.handle(make_event(text="My flight to Tokyo leaves on Friday evening."))

    assert isinstance(response, PresentedReply)
    assert response.title == "Review message.md"
    assert "Type: record" in response.text
    assert sources.list_all() == []


def test_shared_link_is_staged_without_fetching_or_saving(tmp_path: Path) -> None:
    database_path = tmp_path / "steward.db"
    initialize_database(database_path)
    sources = SourceRepository(database_path)
    activity = ActivityService(database_path)
    capture_service = InboxCaptureService(tmp_path / "vault" / "inbox", sources, activity_service=activity)
    application = StewardEventApplication(
        StewardQuestionApplication(FakeGraph()), StewardCaptureApplication(capture_service),
        provisional_intake_application=StewardProvisionalIntakeApplication(
            ProvisionalIntakeService(
                tmp_path / ".steward" / "cache" / "intake", ProvisionalIntakeRepository(database_path),
                capture_service, activity, PrivacyService(database_path),
            )
        ),
    )

    response = application.handle(make_event(text="https://example.test/useful-paper"))

    assert isinstance(response, PresentedReply)
    assert "Type: reference" in response.text
    assert "No content was sent to a model" in response.text
    assert sources.list_all() == []


def test_accepted_flight_intake_immediately_offers_a_record_review(tmp_path: Path) -> None:
    database_path = tmp_path / "steward.db"
    initialize_database(database_path)
    sources = SourceRepository(database_path)
    fragments = SourceFragmentRepository(database_path)
    activity = ActivityService(database_path)
    capture_service = InboxCaptureService(tmp_path / "vault" / "inbox", sources, fragments, activity)
    proposals = ActionProposalRepository(database_path)
    application = StewardEventApplication(
        StewardQuestionApplication(FakeGraph()), StewardCaptureApplication(capture_service),
        provisional_intake_application=StewardProvisionalIntakeApplication(
            ProvisionalIntakeService(
                tmp_path / ".steward" / "cache" / "intake", ProvisionalIntakeRepository(database_path),
                capture_service, activity, PrivacyService(database_path),
            )
        ),
        record_application=StewardRecordApplication(RecordService(database_path), fragments, proposals, activity),
    )

    staged = application.handle(make_event(
        text="Flight SQ638\nDeparture: Singapore\nArrival: Tokyo\nBooking Reference: ABC123"
    ))
    reviewed = application.handle(make_event(text="/intake_accept 1"))

    assert isinstance(staged, PresentedReply)
    assert isinstance(reviewed, PresentedReply)
    assert reviewed.title == "Review travel record"
    assert "Saved to Inbox" in reviewed.text
    assert "flight: SQ638" in reviewed.text
    assert proposals.get(1) is not None
    assert any(action.command == "/source 1" for action in reviewed.actions)
    assert any(action.command == "/organize" for action in reviewed.actions)
    assert RecordService(database_path).list_travel_records() == []


def test_provisional_intake_failure_does_not_disclose_a_local_staging_path() -> None:
    class UnavailableService:
        def accept(self, _intake_id: int, _event: IncomingEvent) -> CaptureResult:
            raise OSError("C:/private/staging/telegram-100-1.pdf is locked")

    response = StewardProvisionalIntakeApplication(UnavailableService()).handle_command(  # type: ignore[arg-type]
        make_event(text="/intake_accept 1")
    )

    assert response == (
        "Could not update this provisional intake because local staging is temporarily unavailable. Try again later."
    )
    assert "C:/private" not in response


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
    assert "Suggested destination" in paused.text
    assert paused.title == "Organize telegram-100-11-Steward-notes.md"
    assert "workspace Steward" in paused.text
    assert str(tmp_path) not in paused.text
    assert [action.command for action in paused.actions] == [
        "/organization_accept 1", "/organization_context 1", "/organization_new_workspace 1",
        "/organization_keep_inbox 1", "/organization_reject 1"
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

    assert accepted == "Moved the source to Steward."
    assert (tmp_path / "vault" / "projects" / "Steward" / "telegram-100-11-Steward-notes.md").is_file()
    assert proposals.get(1).status == "accepted"


def test_telegram_attachment_intake_to_organization_is_a_reviewed_end_to_end_flow(tmp_path: Path) -> None:
    """Exercise the Telegram-shaped path from an uploaded original to one move."""
    database = tmp_path / ".steward" / "steward.db"
    initialize_database(database)
    inbox = tmp_path / "vault" / "inbox"
    sources = SourceRepository(database)
    activity = ActivityService(database)
    privacy = PrivacyService(database)
    capture_service = InboxCaptureService(inbox, sources, activity_service=activity)
    provisional = StewardProvisionalIntakeApplication(
        ProvisionalIntakeService(
            tmp_path / ".steward" / "cache" / "intake",
            ProvisionalIntakeRepository(database),
            capture_service,
            activity,
            privacy,
        )
    )
    workspaces = WorkspaceRepository(database)
    workspaces.create("CS3210")
    proposals = OrganizationProposalRepository(database)
    approval_service = OrganizationApprovalService(
        proposals, sources, FileMutationService(sources, activity), activity
    )
    organization = StewardOrganizationApprovalApplication(
        proposals,
        workspaces,
        OrganizationApprovalThreadRepository(database),
        activity,
        build_organization_approval_graph(
            proposals, checkpointer=InMemorySaver(), review_proposal=approval_service.review,
        ),
        source_repository=sources,
        inbox_dir=inbox,
    )
    application = StewardEventApplication(
        StewardQuestionApplication(FakeGraph()),
        StewardCaptureApplication(capture_service),
        provisional_intake_application=provisional,
        organization_approval_application=organization,
    )
    # The filename deliberately does not name the workspace: the user's staged
    # context, not an incidental filename match, must shape this proposal.
    original = tmp_path / "lecture-notes.md"
    original.write_text("# OpenMP\n\nScheduling notes.", encoding="utf-8")
    upload = IncomingEvent(
        "telegram:901", "telegram", "100", "901", None,
        datetime(2026, 9, 10, tzinfo=UTC), None, (original.name,),
    )

    staged = application.handle_file(upload, original)

    assert isinstance(staged, PresentedReply)
    assert sources.list_all() == []
    assert original.is_file()
    selected = application.handle(make_event(text="/intake_analysis 1 local"))
    assert isinstance(selected, PresentedReply)
    assert "local" in selected.text
    contextualized = application.handle(make_event(text="/intake_context 1 These are for CS3210."))
    assert isinstance(contextualized, PresentedReply)
    assert "CS3210" in contextualized.text
    paused = application.handle(make_event(text="/intake_accept 1"))
    assert isinstance(paused, PresentedReply)
    assert "Suggested destination" in paused.text
    assert "Your added context selected the existing workspace 'CS3210'." in paused.text
    assert paused.title == "Organize telegram-100-901-lecture-notes.md"
    source = sources.get_by_id(1)
    assert source is not None and source.path.parent == inbox
    assert privacy.rule_for(1) is PrivacyRule.LOCAL_MODEL_ONLY

    accepted = application.handle(make_event(text="/organization_accept 1"))

    assert accepted == "Moved telegram-100-901-lecture-notes.md to CS3210."
    assert not source.path.exists()
    assert (tmp_path / "vault" / "projects" / "CS3210" / source.path.name).is_file()
    assert proposals.get(1).status == "accepted"
    assert {event.event_type for event in activity.list_recent()} >= {
        ActivityType.SOURCE_MOVED, ActivityType.ORGANIZATION_ACCEPTED,
    }


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

    assert accepted == "Moved Steward-design.md to Steward."
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
    assert response.title == "Organize CS3210-openmp.md"
    assert "Suggested destination" in response.text
    assert proposals.get(1).source_id == source.id
    assert source_path.is_file()
    listed = application.handle(make_event(text="/organization_proposals"))
    assert "1: source 1 (CS3210-openmp.md) -> workspace 1 [pending]" in listed
    assert str(tmp_path) not in listed


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

    assert response == (
        "I am waiting for your decision. Reply `yes`, `no`, `what is this?`, "
        "or name the workspace you want."
    )

    # Browsing another item must not leave a hidden move armed for a bare yes.
    contexts = ReviewContextRepository(database_path)
    class MustNotExecute:
        def invoke(self, *_args, **_kwargs):
            raise AssertionError("unexpected approval")

    guarded = StewardOrganizationApprovalApplication(
        proposals, WorkspaceRepository(database_path), threads, ActivityService(database_path),
        MustNotExecute(),
        contexts=contexts,
    )
    contexts.set("telegram", "100", "source", source.id)
    assert "Open the organization review" in guarded.handle_decision(make_event(text="yes"))
    assert guarded.handle_decision(make_event(text="What is a TLB?")) is None
    assert proposals.get(proposal_id).status == "pending"
    assert source_path.read_text(encoding="utf-8") == "note"


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
        contexts=ReviewContextRepository(database_path),
    )

    response = app.begin(make_event(text="/save unrelated"), CaptureResult(source, duplicate=False))

    assert isinstance(response, PresentedReply)
    assert "original stays in Inbox" in response.text
    assert any(action.label == "Change workspace" for action in response.actions)
    assert proposals.get(1).status == "pending"
    prompt = app.handle_decision(make_event(text="/organization_context 1"))
    assert isinstance(prompt, PresentedReply)
    assert prompt.title == "Change workspace"
    assert prompt.actions[0].command == "/organization_target 1 1"
    revised = app.handle_followup(make_event(text="CS3210 lecture notes"))
    assert isinstance(revised, PresentedReply)
    assert revised.title == "Organize unrelated.md"
    assert "Suggested destination" in revised.text
    assert "Your context: CS3210 lecture notes" in revised.text
    assert proposals.get(1).status == "rejected"
    accepted = app.handle_decision(make_event(text="/organization_accept 2"))
    assert accepted == "Moved unrelated.md to CS3210."
    assert (tmp_path / "vault" / "projects" / "CS3210" / "unrelated.md").is_file()


def test_ambiguous_organization_context_keeps_the_original_proposal_pending(tmp_path: Path) -> None:
    database = tmp_path / "steward.db"; initialize_database(database)
    inbox = tmp_path / "vault" / "inbox"; inbox.mkdir(parents=True)
    source_path = inbox / "notes.md"; source_path.write_text("notes", encoding="utf-8")
    now = datetime(2026, 9, 10, tzinfo=UTC)
    sources = SourceRepository(database)
    source = sources.add(Source(None, source_path, "d" * 64, SourceType.MARKDOWN, 5, now, now, now))
    workspaces = WorkspaceRepository(database)
    workspaces.create("CS3210"); workspaces.create("CS4226")
    proposals = OrganizationProposalRepository(database); activity = ActivityService(database)
    application = StewardOrganizationApprovalApplication(
        proposals, workspaces, OrganizationApprovalThreadRepository(database), activity,
        build_organization_approval_graph(
            proposals, checkpointer=InMemorySaver(),
            review_proposal=OrganizationApprovalService(
                proposals, sources, FileMutationService(sources, activity), activity, workspaces
            ).review,
        ),
        source_repository=sources, contexts=ReviewContextRepository(database),
    )
    application.begin(make_event(text="/save"), CaptureResult(source, duplicate=False))

    response = application.handle_decision(
        make_event(text="put it with my CS3210 and CS4226 course material")
    )

    assert isinstance(response, PresentedReply)
    assert response.title == "Change workspace"
    assert "current proposal has not changed" in response.text
    assert proposals.get(1).status == "pending"
    assert proposals.get(2) is None
    assert source_path.is_file()


def test_uncertain_capture_can_propose_a_new_workspace_then_move_after_review(tmp_path: Path) -> None:
    database = tmp_path / "steward.db"; checkpoints = tmp_path / "checkpoints.db"; initialize_database(database)
    now = datetime(2026, 9, 10, tzinfo=UTC)
    inbox = tmp_path / "vault" / "inbox"; inbox.mkdir(parents=True)
    path = inbox / "distributed-systems.md"; path.write_text("note", encoding="utf-8")
    sources = SourceRepository(database)
    source = sources.add(Source(None, path, "e" * 64, SourceType.MARKDOWN, 4, now, now, now))
    workspaces = WorkspaceRepository(database); proposals = OrganizationProposalRepository(database); activity = ActivityService(database)
    approval = OrganizationApprovalService(proposals, sources, FileMutationService(sources, activity), activity, workspaces)
    first_connection = sqlite3.connect(checkpoints, check_same_thread=False)
    app = StewardOrganizationApprovalApplication(
        proposals, workspaces, OrganizationApprovalThreadRepository(database), activity,
        build_organization_approval_graph(proposals, checkpointer=SqliteSaver(first_connection), review_proposal=approval.review),
        source_repository=sources,
        contexts=ReviewContextRepository(database),
    )

    app.begin(make_event(text="/save"), CaptureResult(source, duplicate=False))
    prompt = app.handle_decision(make_event(text="/organization_new_workspace 1"))
    assert isinstance(prompt, PresentedReply)
    assert prompt.title == "Name new workspace"
    assert WorkspaceRepository(database).list_all() == []
    first_connection.close()
    restarted_connection = sqlite3.connect(checkpoints, check_same_thread=False)
    app = StewardOrganizationApprovalApplication(
        proposals, workspaces, OrganizationApprovalThreadRepository(database), activity,
        build_organization_approval_graph(
            proposals, checkpointer=SqliteSaver(restarted_connection), review_proposal=approval.review
        ),
        source_repository=sources,
        contexts=ReviewContextRepository(database),
    )
    revised = app.handle_followup(make_event(text="Distributed Systems"))

    assert isinstance(revised, PresentedReply)
    assert proposals.get(2).workspace_name == "Distributed Systems"
    assert WorkspaceRepository(database).list_all() == []
    assert app.handle_decision(make_event(text="/organization_accept 2")) == "Moved distributed-systems.md to Distributed Systems."
    restarted_connection.close()
    assert [workspace.name for workspace in WorkspaceRepository(database).list_all()] == ["Distributed Systems"]
    assert (tmp_path / "vault" / "projects" / "Distributed Systems" / "distributed-systems.md").is_file()


def test_organization_workspace_picker_is_paginated_and_selection_only_revises(tmp_path: Path) -> None:
    database = tmp_path / "steward.db"; initialize_database(database)
    now = datetime(2026, 9, 10, tzinfo=UTC)
    inbox = tmp_path / "vault" / "inbox"; inbox.mkdir(parents=True)
    source_path = inbox / "notes.md"; source_path.write_text("notes", encoding="utf-8")
    sources = SourceRepository(database)
    source = sources.add(Source(None, source_path, "c" * 64, SourceType.MARKDOWN, 5, now, now, now))
    workspaces = WorkspaceRepository(database)
    for index in range(8):
        workspaces.create(f"Workspace {index}")
    proposals = OrganizationProposalRepository(database); activity = ActivityService(database)
    approval = OrganizationApprovalService(
        proposals, sources, FileMutationService(sources, activity), activity, workspaces
    )
    app = StewardOrganizationApprovalApplication(
        proposals, workspaces, OrganizationApprovalThreadRepository(database), activity,
        build_organization_approval_graph(
            proposals, checkpointer=InMemorySaver(), review_proposal=approval.review
        ),
        source_repository=sources, contexts=ReviewContextRepository(database),
    )
    app.begin(make_event(text="/save"), CaptureResult(source, duplicate=False))

    first = app.handle_decision(make_event(text="/organization_context 1"))
    assert isinstance(first, PresentedReply)
    assert "Page 1 of 2" in first.text
    next_command = next(action.command for action in first.actions if action.label == "Next")
    second = app.handle_decision(make_event(text=next_command))
    assert isinstance(second, PresentedReply)
    assert "Workspace 7" in second.text
    choose_command = next(action.command for action in second.actions if action.label == "Choose 2")

    missing = app.handle_decision(make_event(text="/organization_target 1 999"))
    assert missing == "That workspace is no longer available. Choose another target."
    assert proposals.get(1).status == "pending"
    assert source_path.is_file()

    revised = app.handle_decision(make_event(text=choose_command))

    assert isinstance(revised, PresentedReply)
    assert proposals.get(1).status == "rejected"
    assert proposals.get(2).workspace_id == 8
    assert source_path.is_file()
    assert workspaces.list_source_ids(8) == ()


def test_telegram_organization_can_replace_a_move_with_an_accepted_inbox_outcome(tmp_path: Path) -> None:
    database = tmp_path / "steward.db"; initialize_database(database)
    now = datetime(2026, 9, 10, tzinfo=UTC)
    inbox = tmp_path / "vault" / "inbox"; inbox.mkdir(parents=True)
    source_path = inbox / "cs3210-notes.md"; source_path.write_text("notes", encoding="utf-8")
    sources = SourceRepository(database)
    source = sources.add(Source(None, source_path, "a" * 64, SourceType.MARKDOWN, 5, now, now, now))
    workspaces = WorkspaceRepository(database); workspaces.create("CS3210")
    proposals = OrganizationProposalRepository(database); threads = OrganizationApprovalThreadRepository(database)
    activity = ActivityService(database)
    approval = OrganizationApprovalService(
        proposals, sources, FileMutationService(sources, activity), activity, workspaces
    )
    application = StewardOrganizationApprovalApplication(
        proposals, workspaces, threads, activity,
        build_organization_approval_graph(proposals, checkpointer=InMemorySaver(), review_proposal=approval.review),
        source_repository=sources,
    )

    initial = application.begin(make_event(text="/save"), CaptureResult(source, duplicate=False))

    assert isinstance(initial, PresentedReply)
    assert "Suggested destination" in initial.text
    revised = application.handle_decision(make_event(text="/organization_keep_inbox 1"))
    assert isinstance(revised, PresentedReply)
    assert "original stays in Inbox" in revised.text
    assert proposals.get(1).status == "rejected"
    assert proposals.get(2).proposal_type == "keep_in_inbox"
    assert source_path.is_file()

    assert application.handle_decision(make_event(text="/organization_accept 2")) == "Kept cs3210-notes.md in Inbox."
    assert source_path.is_file()
    assert proposals.get(2).status == "accepted"


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
        contexts=ReviewContextRepository(database),
    )

    first.begin(make_event(text="/save"), CaptureResult(source, duplicate=False))
    prompt = first.handle_decision(make_event(text="/organization_context 1"))
    assert isinstance(prompt, PresentedReply)
    first_connection.close()
    restarted_connection = sqlite3.connect(checkpoints, check_same_thread=False)
    restarted = StewardOrganizationApprovalApplication(
        proposals, workspaces, OrganizationApprovalThreadRepository(database), activity,
        build_organization_approval_graph(proposals, checkpointer=SqliteSaver(restarted_connection), review_proposal=approval.review),
        source_repository=sources,
        contexts=ReviewContextRepository(database),
    )

    revised = restarted.handle_followup(make_event(text="CS3210"))
    assert isinstance(revised, PresentedReply)
    assert restarted.handle_decision(make_event(text="/organization_accept 2")) == "Moved unrelated.md to CS3210."
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
    assert response.title == "Organize notes.md"
    assert "Suggested destination" in response.text
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

    assert isinstance(listed, PresentedReply)
    assert listed.title == "Pending actions"
    assert "Create workspace Compiler Project" in listed.text
    assert listed.actions[0].command == f"/review action {proposal.id}"
    assert isinstance(accepted, PresentedReply)
    assert accepted.title == "Workspace created"
    assert "Compiler Project is now available" in accepted.text
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
    assert "No event has been created" in response.text
    assert "Flight SQ638" in response.text and "Singapore → Tokyo" in response.text
    assert "9 Sep 2026" in response.text
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
    assert "Submit CS3210 lab" in response.text
    assert "18 Sep 2026" in response.text and "15-minute deadline marker" in response.text
    assert "No event has been created" in response.text
    assert repository.get(1).action_type == "create_calendar_task_event"


def test_telegram_can_approve_a_task_calendar_proposal_with_the_calendar_writer(tmp_path: Path) -> None:
    database_path = tmp_path / "steward.db"; initialize_database(database_path)
    activity = ActivityService(database_path)
    tasks = TaskService(database_path)
    task = tasks.create("Submit CS3210 lab", due_at=datetime(2026, 9, 18, 15, 59, tzinfo=UTC))
    repository = ActionProposalRepository(database_path)
    proposals = CalendarEventProposalService(repository, RecordService(database_path), activity, tasks)

    class Writer:
        received_task_id: int | None = None

        def create_task_deadline_event(self, received_task):
            self.received_task_id = received_task.id

    writer = Writer()
    pending = proposals.propose_task_event(task.id or 0)
    app = StewardActionProposalApplication(
        repository,
        ActionProposalService(repository, WorkspaceRepository(database_path), activity),
        proposals,
        calendar_writer_factory=lambda: writer,  # type: ignore[arg-type]
    )

    response = app.handle_command(make_event(text=f"/approve_action {pending.id}"))

    assert isinstance(response, PresentedReply)
    assert response.title == "Calendar event created"
    assert writer.received_task_id == task.id
    assert repository.get(pending.id or 0).status == "accepted"


def test_telegram_can_explicitly_import_one_drive_file() -> None:
    class Importer:
        def __init__(self) -> None:
            self.file_ids: list[str] = []

        def import_file(self, file_id: str) -> CaptureResult:
            self.file_ids.append(file_id)
            return CaptureResult(
                type("Source", (), {"id": 42, "path": Path("vault/inbox/drive-import-file-42-note.pdf")})(),
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
    assert isinstance(response, PresentedReply)
    assert response.title == "drive-import-file-42-note.pdf"
    assert "Imported from Drive to Inbox" in response.text
    assert [action.command for action in response.actions] == [
        "/source_content 42", "/source 42", "/source_workspaces 42",
    ]


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
    from steward.search_page import SearchPage
    class Importer:
        def search_page(self, query, *, page_token=None):
            assert query == "parallel"
            return SearchPage((type("DriveFile", (), {"id": "file-42", "name": "Parallel Notes.pdf"})(),))

    response = StewardDriveImportApplication(Importer()).handle_command(make_event(text="/drive_search parallel"))

    assert isinstance(response, PresentedReply)
    assert "file-42: Parallel Notes.pdf" in response.text
    assert response.actions[0].command == "/drive_import file-42"


def test_telegram_can_explicitly_import_one_gmail_message() -> None:
    class Importer:
        def import_message(self, message_id: str) -> CaptureResult:
            assert message_id == "mail-42"
            return CaptureResult(type("Source", (), {"id": 43, "path": Path("vault/inbox/gmail-import-mail-42.eml")})(), False)

    application = StewardGmailImportApplication(Importer())

    response = application.handle_command(make_event(text="/gmail_import mail-42"))
    assert isinstance(response, PresentedReply)
    assert response.title == "gmail-import-mail-42.eml"
    assert "Imported from Gmail to Inbox" in response.text
    assert [action.command for action in response.actions] == [
        "/source_content 43", "/source 43", "/source_workspaces 43",
    ]
    assert application.handle_command(make_event(text="/gmail_import")) == (
        "Use /gmail_import followed by one Gmail message ID."
    )


def test_telegram_external_import_failures_do_not_disclose_local_diagnostics() -> None:
    class DriveImporter:
        def import_file(self, _file_id: str) -> CaptureResult:
            raise OSError("C:/private/google-drive-token.json is unreadable")

        def search_page(self, _query: str, *, page_token=None):
            raise RuntimeError("C:/private/client-secret.json was rejected")

    class GmailImporter:
        def import_message(self, _message_id: str) -> CaptureResult:
            raise OSError("C:/private/gmail-token.json is unreadable")

        def search_page(self, _query: str, *, page_token=None):
            raise RuntimeError("C:/private/client-secret.json was rejected")

    drive = StewardDriveImportApplication(DriveImporter())
    gmail = StewardGmailImportApplication(GmailImporter())

    replies = (
        drive.handle_command(make_event(text="/drive_import file-42")),
        drive.handle_command(make_event(text="/drive_search OpenMP")),
        gmail.handle_command(make_event(text="/gmail_import mail-42")),
        gmail.handle_command(make_event(text="/gmail_search OpenMP")),
    )

    assert all(isinstance(reply, PresentedReply) for reply in replies)
    assert all("C:/private" not in reply.text for reply in replies)
    assert all("Do not send tokens" in reply.text for reply in replies)
    assert [reply.actions[0].command for reply in replies] == [
        "/drive_import file-42", "/drive_search OpenMP", "/gmail_import mail-42", "/gmail_search OpenMP",
    ]


def test_telegram_gmail_search_offers_explicit_individual_imports() -> None:
    from steward.search_page import SearchPage
    class Importer:
        def search_page(self, query, *, page_token=None):
            assert query == "OpenMP"
            return SearchPage((type("GmailMessage", (), {"id": "mail-42", "subject": "OpenMP assignment"})(),))

    response = StewardGmailImportApplication(Importer()).handle_command(make_event(text="/gmail_search OpenMP"))

    assert isinstance(response, PresentedReply)
    assert "mail-42: OpenMP assignment" in response.text
    assert response.actions[0].command == "/gmail_import mail-42"


def test_external_search_continuation_survives_restart_and_does_not_import(tmp_path) -> None:
    from steward.search_page import SearchPage
    from steward.telegram.callbacks import TelegramCallbackRepository

    database = tmp_path / "callbacks.db"
    initialize_database(database)
    query = 'subject:"parallel notes" 日本語'
    cursor = 'opaque/+= "cursor"'
    for prefix, application_type in (("drive", StewardDriveImportApplication), ("gmail", StewardGmailImportApplication)):
        calls = []
        class Importer:
            fail = False
            def search_page(self, search_query, *, page_token=None):
                calls.append((search_query, page_token))
                assert search_query == query
                if self.fail:
                    raise RuntimeError("secret-token at C:/private/token.json")
                if page_token is None:
                    return SearchPage((), cursor)
                assert page_token == cursor
                item = type("Item", (), {"id": "selected-6", "name": "Notes.pdf", "subject": "Notes"})()
                return SearchPage((item,))
            def import_file(self, _identifier):
                raise AssertionError("Browsing must not download")
            import_message = import_file

        first = application_type(Importer()).handle_command(make_event(text=f"/{prefix}_search {query}"))
        assert "No items on this page" in first.text
        more = next(action for action in first.actions if action.label == "More results")
        saved = TelegramCallbackRepository(database).create("test-chat", more.command)
        repository = TelegramCallbackRepository(database)
        assert repository.resolve(saved.token, "another-chat") is None
        restored = repository.resolve(saved.token, "test-chat")
        importer = Importer()
        application = application_type(importer)
        page = application.handle_command(make_event(text=restored.command))
        assert page.actions[0].label == "Import 1"
        assert page.actions[0].command == f"/{prefix}_import selected-6"
        assert not any(action.label == "More results" for action in page.actions)
        assert calls == [(query, None), (query, cursor)]
        importer.fail = True
        failure = application.handle_command(make_event(text=restored.command))
        assert "secret-token" not in failure.text and "C:/private" not in failure.text
        assert failure.actions[0].command == restored.command
        assert failure.actions[-1].command == f"/{prefix}_search {query}"
        before = len(calls)
        for malformed in ('null', '{}', '[1,2]', '["query"]'):
            invalid = application.handle_command(make_event(text=f"/{prefix}_page {malformed}"))
            assert "invalid" in invalid
        assert len(calls) == before
