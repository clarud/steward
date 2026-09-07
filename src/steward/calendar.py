"""Google Calendar OAuth and read-only service boundary."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol


GOOGLE_CALENDAR_READONLY_SCOPE = "https://www.googleapis.com/auth/calendar.readonly"


class CalendarApi(Protocol):
    """The small Google Calendar API surface Steward needs for reads."""

    def events(self) -> Any: ...


@dataclass(frozen=True, slots=True)
class CalendarEvent:
    """A current Calendar event owned authoritatively by Google."""

    id: str
    summary: str
    start: str
    end: str
    html_link: str | None


class CalendarService:
    """Read current Google Calendar state without persisting a stale mirror."""

    def __init__(self, client: CalendarApi, *, calendar_id: str = "primary") -> None:
        if not calendar_id.strip():
            raise ValueError("Calendar ID must not be empty.")
        self._client = client
        self._calendar_id = calendar_id

    def search(
        self,
        query: str = "",
        *,
        time_min: datetime | None = None,
        time_max: datetime | None = None,
        limit: int = 10,
    ) -> tuple[CalendarEvent, ...]:
        """Search current events, optionally within a timezone-aware time range."""
        if not 1 <= limit <= 100:
            raise ValueError("Calendar result limit must be between 1 and 100.")
        self._validate_time(time_min, "time_min")
        self._validate_time(time_max, "time_max")
        if time_min and time_max and time_min > time_max:
            raise ValueError("time_min must be before time_max.")
        request: dict[str, object] = {
            "calendarId": self._calendar_id,
            "singleEvents": True,
            "orderBy": "startTime",
            "maxResults": limit,
        }
        if query.strip():
            request["q"] = query.strip()
        if time_min is not None:
            request["timeMin"] = time_min.isoformat()
        if time_max is not None:
            request["timeMax"] = time_max.isoformat()
        result = self._client.events().list(**request).execute()
        return tuple(self._event_from_api(item) for item in result.get("items", []))

    def get_event(self, event_id: str) -> CalendarEvent:
        """Fetch current event state directly from Google by external ID."""
        if not event_id.strip():
            raise ValueError("Calendar event ID must not be empty.")
        item = self._client.events().get(
            calendarId=self._calendar_id, eventId=event_id
        ).execute()
        return self._event_from_api(item)

    @staticmethod
    def _validate_time(value: datetime | None, name: str) -> None:
        if value is not None and value.tzinfo is None:
            raise ValueError(f"{name} must include a timezone.")

    @staticmethod
    def _event_from_api(item: dict[str, object]) -> CalendarEvent:
        start = item.get("start")
        end = item.get("end")
        if not isinstance(start, dict) or not isinstance(end, dict):
            raise ValueError("Calendar event is missing start or end data.")
        start_value = start.get("dateTime") or start.get("date")
        end_value = end.get("dateTime") or end.get("date")
        if not all((item.get("id"), start_value, end_value)):
            raise ValueError("Calendar event is missing required fields.")
        return CalendarEvent(
            id=str(item["id"]),
            summary=str(item.get("summary") or "(untitled event)"),
            start=str(start_value),
            end=str(end_value),
            html_link=str(item["htmlLink"]) if item.get("htmlLink") else None,
        )


def authorize_google_calendar(
    client_secrets_path: Path,
    token_path: Path,
    *,
    scopes: tuple[str, ...] = (GOOGLE_CALENDAR_READONLY_SCOPE,),
) -> CalendarApi:
    """Run local OAuth if needed, refresh a token, and construct Google's client."""
    if not client_secrets_path.is_file():
        raise FileNotFoundError(client_secrets_path)
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build

    credentials = None
    if token_path.is_file():
        credentials = Credentials.from_authorized_user_file(str(token_path), scopes)
    if credentials and credentials.expired and credentials.refresh_token:
        credentials.refresh(Request())
    if not credentials or not credentials.valid:
        flow = InstalledAppFlow.from_client_secrets_file(str(client_secrets_path), scopes)
        credentials = flow.run_local_server(port=0)
    token_path.parent.mkdir(parents=True, exist_ok=True)
    token_path.write_text(credentials.to_json(), encoding="utf-8")
    return build("calendar", "v3", credentials=credentials)
