"""Read-only Google Calendar tools for the Phase 21 agent loop."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime

from langchain_core.tools import BaseTool, tool

from steward.calendar import CalendarService
from steward.tools.policy import ToolDefinition, ToolRisk


CALENDAR_READ_TOOL_DEFINITIONS = [
    ToolDefinition("calendar_search", False, ToolRisk.READ_ONLY, "not applicable: no mutation", "google_calendar", False),
    ToolDefinition("calendar_get_event", False, ToolRisk.READ_ONLY, "not applicable: no mutation", "google_calendar", False),
]


class CalendarReadToolService:
    def __init__(self, calendar: CalendarService | Callable[[], CalendarService]) -> None:
        self._calendar = calendar

    def _service(self) -> CalendarService:
        return self._calendar() if callable(self._calendar) else self._calendar

    def search(self, query: str = "", after: str | None = None, before: str | None = None, limit: int = 10) -> str:
        """Return current Calendar events, parsing ISO-8601 bounds when supplied."""
        try:
            events = self._service().search(
                query,
                time_min=datetime.fromisoformat(after) if after else None,
                time_max=datetime.fromisoformat(before) if before else None,
                limit=limit,
            )
            return json.dumps([self._event(event) for event in events])
        # This is an external adapter boundary. A provider/credential failure
        # must become a ToolMessage payload, not terminate the agent graph.
        except Exception:  # noqa: BLE001 - external Calendar client exceptions are provider-specific
            return json.dumps({"error": "Calendar search is unavailable. Check the request dates and local Calendar authorization or retry later. No current event data was retrieved."})

    def get_event(self, event_id: str) -> str:
        try:
            return json.dumps(self._event(self._service().get_event(event_id)))
        except Exception:  # noqa: BLE001 - see search above
            return json.dumps({"error": "Calendar event lookup is unavailable. The event may be inaccessible, or Calendar authorization or connectivity may need attention. No current event data was retrieved."})

    @staticmethod
    def _event(event) -> dict[str, str | None]:
        return {"id": event.id, "summary": event.summary, "start": event.start, "end": event.end, "html_link": event.html_link}


def build_calendar_read_tools(service: CalendarReadToolService) -> list[BaseTool]:
    @tool
    def calendar_search(query: str = "", after: str | None = None, before: str | None = None, limit: int = 10) -> str:
        """Search current Google Calendar events. Dates must be ISO-8601 with timezone offsets."""
        return service.search(query, after, before, limit)

    @tool
    def calendar_get_event(event_id: str) -> str:
        """Read one current Google Calendar event by its external Google event ID."""
        return service.get_event(event_id)

    return [calendar_search, calendar_get_event]
