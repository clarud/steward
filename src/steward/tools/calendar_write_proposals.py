"""Proposal-only Calendar action exposed to the model tool loop."""

from __future__ import annotations

import json

from langchain_core.tools import BaseTool, tool

from steward.calendar import CalendarEventProposalService
from steward.tools.policy import ToolDefinition, ToolRisk


CALENDAR_PROPOSAL_TOOL_DEFINITIONS = [
    ToolDefinition(
        "propose_create_travel_calendar_event",
        True,
        ToolRisk.SAFE_WRITE,
        "same travel record reuses its pending proposal",
        "google_calendar",
        False,
    )
]


class CalendarProposalToolService:
    """Create a reviewable request, never a remote Calendar event."""

    def __init__(self, proposals: CalendarEventProposalService) -> None:
        self._proposals = proposals

    def propose_create_travel_calendar_event(self, record_id: int) -> str:
        proposal = self._proposals.propose_travel_event(record_id)
        if proposal.id is None:
            raise RuntimeError("Calendar proposal must be persisted before returning to the model.")
        return json.dumps(
            {
                "status": "pending_approval",
                "proposal_id": proposal.id,
                "action_type": proposal.action_type,
                "travel_record_id": record_id,
                "review_command": f"steward calendar-review-travel-event {proposal.id} accepted",
            }
        )


def build_calendar_proposal_tools(service: CalendarProposalToolService) -> list[BaseTool]:
    """Return the narrow, non-executing Calendar proposal schema."""

    @tool
    def propose_create_travel_calendar_event(record_id: int) -> str:
        """Request approval to create a Calendar event from a saved travel record. Never creates it now."""

        return service.propose_create_travel_calendar_event(record_id)

    return [propose_create_travel_calendar_event]
