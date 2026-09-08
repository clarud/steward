from datetime import UTC, datetime
from pathlib import Path

from steward.answer import AnswerCitation
from steward.application import StewardEventApplication, StewardQuestionApplication, TEXT_QUESTION_REQUIRED
from steward.application import StewardCaptureApplication, StewardOrganizationApprovalApplication
from steward.capture import CaptureResult, InboxCaptureService
from steward.events import IncomingEvent
from steward.organization import (
    OrganizationApprovalService,
    OrganizationApprovalThreadRepository,
    OrganizationProposal,
    OrganizationProposalRepository,
)
from steward.actions import FileMutationService
from steward.activity import ActivityService
from steward.graphs import build_organization_approval_graph
from steward.sources import Source, SourceRepository, SourceType
from steward.storage import initialize_database
from steward.workspaces import WorkspaceRepository
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

    assert "Organization proposal 1" in paused
    assert "Reply `accept` or `reject`" in paused
    assert not (tmp_path / "vault" / "projects" / "Steward").exists()

    accepted = application.handle(
        IncomingEvent(
            "telegram:100",
            "telegram",
            "100",
            "12",
            None,
            datetime(2026, 9, 8, tzinfo=UTC),
            "accept",
        )
    )

    assert accepted == "Proposal 1 accepted."
    assert (tmp_path / "vault" / "projects" / "Steward" / "telegram-100-11-Steward-notes.md").is_file()
    assert proposals.get(1).status == "accepted"


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
