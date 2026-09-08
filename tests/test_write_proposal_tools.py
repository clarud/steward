import json

from steward.action_proposals import ActionProposalRepository, ActionProposalService
from steward.activity import ActivityService
from steward.storage import initialize_database
from steward.tools import ActionProposalToolService, build_action_proposal_tools
from steward.workspaces import WorkspaceRepository


def test_workspace_proposal_tool_creates_a_pending_action_not_a_workspace(tmp_path) -> None:
    database_path = tmp_path / "steward.db"; initialize_database(database_path)
    repository = ActionProposalRepository(database_path)
    service = ActionProposalService(
        repository, WorkspaceRepository(database_path), ActivityService(database_path)
    )
    tool = build_action_proposal_tools(ActionProposalToolService(service))[0]

    result = json.loads(tool.invoke({"name": "Compiler Project"}))

    assert result["status"] == "pending_approval"
    assert result["action_type"] == "create_workspace"
    assert result["review_command"] == "steward review-action-proposal 1 accepted"
    assert WorkspaceRepository(database_path).list_all() == []
