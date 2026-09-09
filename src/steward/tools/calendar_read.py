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
        except (OSError, ValueError) as error:
            return json.dumps({"error": f"Calendar search is unavailable: {error}"})
        return json.dumps([self._event(event) for event in events])

    def get_event(self, event_id: str) -> str:
        try:
            return json.dumps(self._event(self._service().get_event(event_id)))
        except (OSError, ValueError) as error:
            return json.dumps({"error": f"Calendar event lookup is unavailable: {error}"})

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
