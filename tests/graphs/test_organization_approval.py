from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command
from steward.graphs import build_organization_approval_graph
from steward.organization import OrganizationApprovalService, OrganizationProposalRepository, OrganizationService
from steward.actions import FileMutationService
from steward.activity import ActivityService
from steward.sources import Source, SourceRepository, SourceType
from steward.storage import initialize_database
from steward.workspaces import Workspace
from datetime import UTC, datetime

class FakeRepository:
    def __init__(self): self.calls = []
    def set_status(self, proposal_id, status): self.calls.append((proposal_id, status))

def test_approval_graph_pauses_then_records_resumed_decision() -> None:
    repository = FakeRepository()
    graph = build_organization_approval_graph(repository, checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": "telegram:100"}}
    paused = graph.invoke({"proposal_id": 3}, config)
    assert "__interrupt__" in paused and repository.calls == []
    result = graph.invoke(Command(resume="accepted"), config)
    assert result["status"] == "accepted"
    assert repository.calls == [(3, "accepted")]


def test_resumed_graph_executes_accepted_proposal_once(tmp_path) -> None:
    database = tmp_path / "steward.db"
    initialize_database(database)
    now = datetime(2026, 9, 8, tzinfo=UTC)
    inbox = tmp_path / "vault" / "inbox"
    inbox.mkdir(parents=True)
    original = inbox / "steward-note.md"
    original.write_text("note", encoding="utf-8")
    sources = SourceRepository(database)
    source = sources.add(Source(None, original.resolve(), "a" * 64, SourceType.MARKDOWN, 4, now, now, now))
    proposal_repository = OrganizationProposalRepository(database)
    proposal_id = proposal_repository.add(OrganizationService().propose(source, [Workspace(1, "Steward", "active", now)]))
    activity = ActivityService(database)
    approval = OrganizationApprovalService(
        proposal_repository, sources, FileMutationService(sources, activity), activity
    )
    graph = build_organization_approval_graph(
        proposal_repository,
        checkpointer=InMemorySaver(),
        review_proposal=approval.review,
    )
    config = {"configurable": {"thread_id": "telegram:100"}}

    graph.invoke({"proposal_id": proposal_id}, config)
    result = graph.invoke(Command(resume="accepted"), config)

    moved = tmp_path / "vault" / "projects" / "Steward" / "steward-note.md"
    assert result["status"] == "accepted"
    assert moved.is_file() and sources.get_by_id(source.id or 0).path == moved.resolve()
    assert proposal_repository.get(proposal_id).status == "accepted"
