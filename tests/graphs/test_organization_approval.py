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
import sqlite3
from contextlib import closing
from langgraph.checkpoint.sqlite import SqliteSaver
from steward.storage import snapshot_database, restore_database
from steward.sources.hashing import hash_file

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


def test_paused_organization_restores_paired_databases_before_explicit_resume(tmp_path) -> None:
    database = tmp_path / "steward.db"
    checkpoints = tmp_path / "checkpoints.db"
    initialize_database(database)
    now = datetime.now(UTC)
    inbox = tmp_path / "vault" / "inbox"
    inbox.mkdir(parents=True)
    original = inbox / "steward-note.md"
    original.write_text("Original evidence", encoding="utf-8")
    expected = original.read_bytes()
    sources = SourceRepository(database)
    source = sources.add(Source(None, original.resolve(), hash_file(original), SourceType.MARKDOWN, len(expected), now, now, now))
    repository = OrganizationProposalRepository(database)
    identifier = repository.add(OrganizationService().propose(source, [Workspace(1, "Steward", "active", now)]))
    config = {"configurable": {"thread_id": "recovery:organization:1"}}

    def graph_for(connection):
        restored_sources = SourceRepository(database)
        restored_repository = OrganizationProposalRepository(database)
        activity = ActivityService(database)
        approval = OrganizationApprovalService(restored_repository, restored_sources,
            FileMutationService(restored_sources, activity), activity)
        return build_organization_approval_graph(restored_repository,
            checkpointer=SqliteSaver(connection), review_proposal=approval.review)

    with closing(sqlite3.connect(checkpoints, check_same_thread=False)) as connection:
        graph = graph_for(connection)
        paused = graph.invoke({"proposal_id": identifier}, config)
        assert "__interrupt__" in paused
    # No writers remain: the two copies describe the same paused workflow.
    operational_backup = snapshot_database(database, tmp_path / "operational-backup.db")
    checkpoint_backup = snapshot_database(checkpoints, tmp_path / "checkpoint-backup.db")
    with closing(sqlite3.connect(checkpoints, check_same_thread=False)) as connection:
        result = graph_for(connection).invoke(Command(resume="rejected"), config)
        assert result["status"] == "rejected"
    assert repository.get(identifier).status == "rejected"

    operational_safety = restore_database(operational_backup, database, tmp_path / "operational-safety.db")
    checkpoint_safety = restore_database(checkpoint_backup, checkpoints, tmp_path / "checkpoint-safety.db")
    assert OrganizationProposalRepository(operational_safety).get(identifier).status == "rejected"
    assert checkpoint_safety.is_file()
    with closing(sqlite3.connect(checkpoint_safety, check_same_thread=False)) as connection:
        safety_state = graph_for(connection).get_state(config)
        assert safety_state.values["status"] == "rejected"
        assert safety_state.next == ()
    assert original.read_bytes() == expected
    assert OrganizationProposalRepository(database).get(identifier).status == "pending"
    with closing(sqlite3.connect(checkpoints, check_same_thread=False)) as connection:
        restored = graph_for(connection)
        assert restored.get_state(config).next == ("request_approval",)
        assert original.is_file()  # Reading/restoring the checkpoint does not approve.
        result = restored.invoke(Command(resume="accepted"), config)
        assert result["status"] == "accepted"
        assert restored.get_state(config).next == ()
    moved = tmp_path / "vault" / "projects" / "Steward" / original.name
    assert moved.read_bytes() == expected and not original.exists()
    assert SourceRepository(database).get_by_id(source.id).path == moved.resolve()
    assert OrganizationProposalRepository(database).get(identifier).status == "accepted"
    assert OrganizationProposalRepository(operational_backup).get(identifier).status == "pending"
