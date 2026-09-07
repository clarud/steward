from datetime import UTC, datetime

import pytest

from steward.calendar import CalendarService
from steward.tools import CalendarReadToolService, build_calendar_read_tools


class FakeRequest:
    def __init__(self, result):
        self._result = result

    def execute(self):
        return self._result


class FakeEvents:
    def __init__(self) -> None:
        self.list_kwargs = None
        self.get_kwargs = None

    def list(self, **kwargs):
        self.list_kwargs = kwargs
        return FakeRequest({"items": [{"id": "event-1", "summary": "Flight", "start": {"dateTime": "2026-10-01T09:00:00+08:00"}, "end": {"dateTime": "2026-10-01T17:00:00+09:00"}, "htmlLink": "https://calendar.example/event-1"}]})

    def get(self, **kwargs):
        self.get_kwargs = kwargs
        return FakeRequest({"id": kwargs["eventId"], "start": {"date": "2026-10-01"}, "end": {"date": "2026-10-02"}})


class FakeCalendarClient:
    def __init__(self) -> None:
        self.events_api = FakeEvents()

    def events(self):
        return self.events_api


def test_calendar_search_requests_current_events_with_time_bounds() -> None:
    client = FakeCalendarClient()
    service = CalendarService(client)

    events = service.search(
        "Tokyo",
        time_min=datetime(2026, 10, 1, tzinfo=UTC),
        time_max=datetime(2026, 10, 2, tzinfo=UTC),
        limit=3,
    )

    assert events[0].summary == "Flight"
    assert events[0].start == "2026-10-01T09:00:00+08:00"
    assert client.events_api.list_kwargs == {
        "calendarId": "primary", "singleEvents": True, "orderBy": "startTime",
        "maxResults": 3, "q": "Tokyo", "timeMin": "2026-10-01T00:00:00+00:00",
        "timeMax": "2026-10-02T00:00:00+00:00",
    }


def test_calendar_get_event_preserves_all_day_dates() -> None:
    client = FakeCalendarClient()
    event = CalendarService(client).get_event("event-2")

    assert event.id == "event-2"
    assert event.summary == "(untitled event)"
    assert event.start == "2026-10-01"
    assert client.events_api.get_kwargs == {"calendarId": "primary", "eventId": "event-2"}


def test_calendar_rejects_naive_and_invalid_ranges() -> None:
    service = CalendarService(FakeCalendarClient())
    with pytest.raises(ValueError, match="timezone"):
        service.search(time_min=datetime(2026, 10, 1))
    with pytest.raises(ValueError, match="before"):
        service.search(time_min=datetime(2026, 10, 2, tzinfo=UTC), time_max=datetime(2026, 10, 1, tzinfo=UTC))


def test_calendar_agent_tools_are_read_only_adapters() -> None:
    service = CalendarReadToolService(CalendarService(FakeCalendarClient()))

    result = service.search("Flight", limit=2)

    assert '"id": "event-1"' in result
    assert [tool.name for tool in build_calendar_read_tools(service)] == ["calendar_search", "calendar_get_event"]
