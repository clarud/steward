"""Low-impact agent tools that create reviewable proposals, never final actions."""

from __future__ import annotations

import json

from langchain_core.tools import BaseTool, tool

from steward.action_proposals import ActionProposalService
from steward.tools.policy import ToolDefinition, ToolRisk


ACTION_PROPOSAL_TOOL_DEFINITIONS = [
    ToolDefinition(
        "propose_create_workspace",
        True,
        ToolRisk.SAFE_WRITE,
        "same workspace name reuses its pending proposal",
        None,
        False,
    )
]


class ActionProposalToolService:
    """Expose proposal creation as a deliberately non-executing model tool."""

    def __init__(self, service: ActionProposalService) -> None:
        self._service = service

    def propose_create_workspace(self, name: str) -> str:
        """Request human approval to create a workspace; it does not create one."""

        proposal, existing = self._service.propose_workspace_creation(name)
        if existing is not None:
            return json.dumps(
                {
                    "status": "already_exists",
                    "workspace_id": existing.id,
                    "workspace_name": existing.name,
                }
            )
        if proposal is None or proposal.id is None:
            raise RuntimeError("A new workspace proposal must be persisted.")
        return json.dumps(
            {
                "status": "pending_approval",
                "proposal_id": proposal.id,
                "action_type": proposal.action_type,
                "workspace_name": proposal.payload["name"],
                "review_command": f"steward review-action-proposal {proposal.id} accepted",
            }
        )


def build_action_proposal_tools(service: ActionProposalToolService) -> list[BaseTool]:
    """Return proposal-only tools that are safe to expose alongside read tools."""

    @tool
    def propose_create_workspace(name: str) -> str:
        """Create a pending workspace-creation proposal. Never claim the workspace now exists."""

        return service.propose_create_workspace(name)

    return [propose_create_workspace]
