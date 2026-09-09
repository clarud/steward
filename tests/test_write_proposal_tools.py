import json

from steward.action_proposals import ActionProposalRepository, ActionProposalService
from steward.activity import ActivityService
from steward.calendar import CalendarEventProposalService
from steward.records import RecordService, TravelRecord
from steward.sources import Source, SourceRepository, SourceType
from datetime import UTC, datetime
from steward.storage import initialize_database
from steward.tools import ActionProposalToolService, build_action_proposal_tools
from steward.tools import CalendarProposalToolService, build_calendar_proposal_tools
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


def test_calendar_proposal_tool_only_creates_a_pending_action(tmp_path) -> None:
    database_path = tmp_path / "steward.db"; initialize_database(database_path)
    now = datetime(2026, 9, 8, tzinfo=UTC)
    source = SourceRepository(database_path).add(
        Source(None, tmp_path / "trip.pdf", "a" * 64, SourceType.PDF, 0, now, now, now)
    )
    record = RecordService(database_path).create_travel_record(
        TravelRecord(None, source.id or 0, "SQ638", "Singapore", "Tokyo", datetime(2026, 10, 1, 9, tzinfo=UTC), datetime(2026, 10, 1, 17, tzinfo=UTC), None)
    )
    service = CalendarEventProposalService(
        ActionProposalRepository(database_path), RecordService(database_path), ActivityService(database_path)
    )
    tool = build_calendar_proposal_tools(CalendarProposalToolService(service))[0]

    result = json.loads(tool.invoke({"record_id": record.id}))

    assert result["status"] == "pending_approval"
    assert result["action_type"] == "create_calendar_travel_event"
    assert result["review_command"] == "steward calendar-review-travel-event 1 accepted"
