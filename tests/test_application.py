from datetime import UTC, datetime
from pathlib import Path

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
    TEXT_QUESTION_REQUIRED,
)
from steward.action_proposals import ActionProposalRepository, ActionProposalService
from steward.calendar import CalendarEventProposalService
from steward.records import RecordService, TravelRecord
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
from steward.extraction import ExtractionResult, SourceFragment, SourceFragmentRepository
from steward.graphs import build_organization_approval_graph
from steward.sources import Source, SourceRepository, SourceType
from steward.storage import initialize_database
from steward.workspaces import WorkspaceRepository
from steward.retrieval import LexicalSearchService
from steward.presentation import PresentedReply
from steward.intake import ProvisionalIntakeRepository, ProvisionalIntakeService
from steward.knowledge import KnowledgeEnrichmentProposalRepository, KnowledgeService
from steward.roots import SourceRootRepository
from langgraph.checkpoint.memory import InMemorySaver


class FakeGraph:
    def __init__(self) -> None:
        self.inputs: list[dict[str, str]] = []

    def invoke(self, input: dict[str, str], config=None) -> dict[str, str]:
        self.inputs.append(input)
        return {"answer": "A TLB caches address translations. [F1]"}


def make_event(*, text: str | None) -> IncomingEvent:
    return IncomingEvent(
        id="telegram:42",
        platform="telegram",
        chat_id="100",
        message_id="7",
        reply_to_id=None,
        timestamp=datetime(2026, 9, 7, tzinfo=UTC),
        text=text,
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
        f"Sources:\n[F1] {Path('vault/virtual-memory.md')}:lines 4-6 [TLB]"
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
    assert "openmp.md" in application.handle(make_event(text="what is in my inbox"))
    assert "Recent activity" in application.handle(make_event(text="show my recent activity"))
    assert "Search results" in application.handle(make_event(text="find my notes on OpenMP"))
    assert "Inbox" in application.handle(
        make_event(text="organize my inbox")
    )
    assert "Workspace proposal 1" in application.handle(
        make_event(text="create a workspace for jobs")
    )


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
    assert sources.list_all() == []
    accepted = application.handle(
        IncomingEvent(
            "telegram:accept", "telegram", "100", "13", None,
            datetime(2026, 9, 9, tzinfo=UTC), "/intake_accept 1",
        )
    )
    assert "Saved to Inbox" in accepted
    assert len(sources.list_all()) == 1


def test_provisional_intake_context_command_updates_without_saving(tmp_path: Path) -> None:
    database_path = tmp_path / "steward.db"
    initialize_database(database_path)
    sources = SourceRepository(database_path)
    activity = ActivityService(database_path)
    capture_service = InboxCaptureService(tmp_path / "vault" / "inbox", sources, activity_service=activity)
    provisional = StewardProvisionalIntakeApplication(
        ProvisionalIntakeService(
            tmp_path / ".steward" / "cache" / "intake", ProvisionalIntakeRepository(database_path),
            capture_service, activity,
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

    assert response == "Saved to Inbox: vault\\inbox\\note.pdf"
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


def test_uncertain_capture_remains_in_inbox_without_creating_a_pending_approval(tmp_path: Path) -> None:
    database_path = tmp_path / "steward.db"
    initialize_database(database_path)
    now = datetime(2026, 9, 8, tzinfo=UTC)
    source_path = tmp_path / "vault" / "inbox" / "unrelated.md"; source_path.parent.mkdir(parents=True)
    source_path.write_text("note", encoding="utf-8")
    source = SourceRepository(database_path).add(
        Source(None, source_path.resolve(), "b" * 64, SourceType.MARKDOWN, 4, now, now, now)
    )
    proposals = OrganizationProposalRepository(database_path)
    threads = OrganizationApprovalThreadRepository(database_path)
    app = StewardOrganizationApprovalApplication(
        proposals,
        WorkspaceRepository(database_path),
        threads,
        ActivityService(database_path),
        type("Graph", (), {"invoke": lambda *_args, **_kwargs: {}})(),
    )

    response = app.begin(make_event(text="/save unrelated"), CaptureResult(source, duplicate=False))

    assert response == "No confident organization match was found, so the source remains in Inbox."
    assert proposals.list_all() == []
    assert threads.get_pending("telegram", "100") is None


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
    assert response == "Imported Drive file to Inbox: vault\\inbox\\drive-import-file-42-note.pdf"


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


def test_telegram_can_explicitly_import_one_gmail_message() -> None:
    class Importer:
        def import_message(self, message_id: str) -> CaptureResult:
            assert message_id == "mail-42"
            return CaptureResult(type("Source", (), {"path": Path("vault/inbox/gmail-import-mail-42.eml")})(), False)

    application = StewardGmailImportApplication(Importer())

    assert application.handle_command(make_event(text="/gmail_import mail-42")) == (
        "Imported Gmail message to Inbox: vault\\inbox\\gmail-import-mail-42.eml"
    )
    assert application.handle_command(make_event(text="/gmail_import")) == (
        "Use /gmail_import followed by one Gmail message ID."
    )
