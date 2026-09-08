from pathlib import Path

from steward.action_proposals import ActionProposalRepository, ActionProposalService
from steward.activity import ActivityService, ActivityType
from steward.storage import initialize_database
from steward.workspaces import WorkspaceRepository


def _service(database_path: Path) -> tuple[ActionProposalRepository, ActionProposalService, WorkspaceRepository, ActivityService]:
    repository = ActionProposalRepository(database_path)
    workspaces = WorkspaceRepository(database_path)
    activity = ActivityService(database_path)
    return repository, ActionProposalService(repository, workspaces, activity), workspaces, activity


def test_workspace_action_proposal_is_idempotent_until_reviewed(tmp_path: Path) -> None:
    database_path = tmp_path / "steward.db"; initialize_database(database_path)
    repository, service, workspaces, activity = _service(database_path)

    first, existing = service.propose_workspace_creation("Compiler Project")
    second, repeated_existing = service.propose_workspace_creation("  Compiler   Project ")

    assert existing is None and repeated_existing is None
    assert first is not None and second is not None and first.id == second.id
    assert workspaces.list_all() == []
    assert [event.event_type for event in activity.list_recent()] == [ActivityType.ACTION_PROPOSED]
    assert repository.get(first.id or 0).status == "pending"


def test_accepting_workspace_action_proposal_creates_once_and_audits(tmp_path: Path) -> None:
    database_path = tmp_path / "steward.db"; initialize_database(database_path)
    repository, service, workspaces, activity = _service(database_path)
    proposal, _ = service.propose_workspace_creation("Compiler Project")

    reviewed, workspace = service.review(proposal.id or 0, "accepted")
    repeated, repeated_workspace = service.review(proposal.id or 0, "accepted")

    assert reviewed.status == "accepted"
    assert workspace is not None and workspace.name == "Compiler Project"
    assert repeated.id == reviewed.id
    assert repeated_workspace is not None and repeated_workspace.id == workspace.id
    assert [workspace.name for workspace in workspaces.list_all()] == ["Compiler Project"]
    assert [event.event_type for event in activity.list_recent()] == [
        ActivityType.ACTION_ACCEPTED,
        ActivityType.WORKSPACE_CREATED,
        ActivityType.ACTION_PROPOSED,
    ]


def test_rejecting_workspace_action_proposal_does_not_create_a_workspace(tmp_path: Path) -> None:
    database_path = tmp_path / "steward.db"; initialize_database(database_path)
    repository, service, workspaces, activity = _service(database_path)
    proposal, _ = service.propose_workspace_creation("Compiler Project")

    reviewed, workspace = service.review(proposal.id or 0, "rejected")

    assert reviewed.status == "rejected"
    assert workspace is None
    assert workspaces.list_all() == []
    assert activity.list_recent()[0].event_type is ActivityType.ACTION_REJECTED
