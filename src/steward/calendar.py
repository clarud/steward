"""Google Calendar OAuth and read-only service boundary."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol
import sqlite3

from steward.activity import ActivityService, ActivityType
from steward.action_proposals import ActionProposal, ActionProposalRepository
from steward.records import TravelRecord
from steward.records import RecordService


GOOGLE_CALENDAR_READONLY_SCOPE = "https://www.googleapis.com/auth/calendar.readonly"
GOOGLE_CALENDAR_EVENTS_SCOPE = "https://www.googleapis.com/auth/calendar.events"


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

    def create_event(
        self,
        *,
        summary: str,
        start: datetime,
        end: datetime,
        description: str = "",
        idempotency_key: str | None = None,
    ) -> CalendarEvent:
        """Create one explicitly approved timed event through Google's API."""
        if not summary.strip():
            raise ValueError("Calendar event summary must not be empty.")
        self._validate_time(start, "start")
        self._validate_time(end, "end")
        if start >= end:
            raise ValueError("Calendar event start must be before end.")
        body: dict[str, object] = {
            "summary": summary.strip(),
            "description": description,
            "start": {"dateTime": start.isoformat()},
            "end": {"dateTime": end.isoformat()},
        }
        if idempotency_key:
            body["extendedProperties"] = {
                "private": {"steward_idempotency_key": idempotency_key}
            }
        item = self._client.events().insert(
            calendarId=self._calendar_id,
            body=body,
        ).execute()
        return self._event_from_api(item)

    def find_by_idempotency_key(self, key: str) -> CalendarEvent | None:
        """Find a prior Steward-created event after an interrupted local write."""

        if not key.strip():
            raise ValueError("Calendar idempotency key must not be empty.")
        result = self._client.events().list(
            calendarId=self._calendar_id,
            singleEvents=True,
            maxResults=1,
            privateExtendedProperty=f"steward_idempotency_key={key}",
        ).execute()
        items = result.get("items", [])
        return self._event_from_api(items[0]) if items else None

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
        if credentials and not credentials.has_scopes(scopes):
            credentials = None
    if credentials and credentials.expired and credentials.refresh_token:
        credentials.refresh(Request())
    if not credentials or not credentials.valid:
        flow = InstalledAppFlow.from_client_secrets_file(str(client_secrets_path), scopes)
        credentials = flow.run_local_server(port=0)
    token_path.parent.mkdir(parents=True, exist_ok=True)
    token_path.write_text(credentials.to_json(), encoding="utf-8")
    return build("calendar", "v3", credentials=credentials)


class CalendarWriteService:
    """Create one Calendar event per travel record with duplicate protection."""

    def __init__(self, calendar: CalendarService, database_path: Path, activity: ActivityService) -> None:
        self._calendar = calendar
        self._database_path = database_path
        self._activity = activity

    def create_travel_event(self, record: TravelRecord) -> CalendarEvent:
        if record.id is None:
            raise ValueError("Only persisted travel records can create calendar events.")
        if record.departure_time is None or record.arrival_time is None:
            raise ValueError("Travel records need departure and arrival times for Calendar.")
        key = f"travel-record:{record.id}"
        existing = self._existing(key)
        if existing is not None:
            return self._calendar.get_event(existing)
        recovered = self._calendar.find_by_idempotency_key(key)
        if recovered is not None:
            self._link(key, record.id, recovered.id)
            return recovered
        summary = f"Flight {record.flight_number}" if record.flight_number else "Flight"
        description = "Steward travel record " + str(record.id)
        if record.booking_reference:
            description += f"\nBooking reference: {record.booking_reference}"
        event = self._calendar.create_event(
            summary=summary,
            start=record.departure_time,
            end=record.arrival_time,
            description=description,
            idempotency_key=key,
        )
        self._link(key, record.id, event.id)

        return event

    def _link(self, key: str, record_id: int, event_id: str) -> None:
        with sqlite3.connect(self._database_path) as connection:
            connection.execute("PRAGMA foreign_keys = ON")
            cursor = connection.execute(
                "INSERT OR IGNORE INTO calendar_event_links (idempotency_key, travel_record_id, external_event_id, created_at) VALUES (?, ?, ?, ?)",
                (key, record_id, event_id, datetime.now().astimezone().isoformat()),
            )
        if cursor.rowcount:
            self._activity.record(ActivityType.CALENDAR_EVENT_CREATED, object_id=event_id, details=key)

    def _existing(self, key: str) -> str | None:
        with sqlite3.connect(self._database_path) as connection:
            row = connection.execute(
                "SELECT external_event_id FROM calendar_event_links WHERE idempotency_key = ?", (key,)
            ).fetchone()
        return str(row[0]) if row else None


class CalendarEventProposalService:
    """Persist Calendar requests before an explicit approval can create an event."""

    CREATE_TRAVEL_EVENT = "create_calendar_travel_event"

    def __init__(
        self,
        proposals: ActionProposalRepository,
        records: RecordService,
        activity: ActivityService,
    ) -> None:
        self._proposals = proposals
        self._records = records
        self._activity = activity

    def propose_travel_event(self, record_id: int) -> ActionProposal:
        record = self._record(record_id)
        self._validate_record(record)
        payload = {"record_id": str(record_id)}
        existing = self._proposals.find_pending(self.CREATE_TRAVEL_EVENT, payload)
        if existing is not None:
            return existing
        proposal = self._proposals.add(self.CREATE_TRAVEL_EVENT, payload)
        self._activity.record(
            ActivityType.ACTION_PROPOSED,
            object_id=str(proposal.id),
            details=f"Create Calendar event for travel record {record_id}",
        )
        return proposal

    def review(
        self,
        proposal_id: int,
        decision: str,
        calendar_writer: CalendarWriteService | None = None,
    ) -> ActionProposal:
        """Reject locally, or create exactly one remote event after acceptance."""

        if decision not in {"accepted", "rejected"}:
            raise ValueError("Calendar proposal decision must be accepted or rejected.")
        proposal = self._proposal(proposal_id)
        if proposal.status == decision:
            return proposal
        if proposal.status != "pending":
            raise ValueError(f"Action proposal {proposal_id} was already {proposal.status}.")
        if decision == "accepted":
            if calendar_writer is None:
                raise ValueError("Calendar authorization is required to accept this proposal.")
            calendar_writer.create_travel_event(self._record(int(proposal.payload["record_id"])))
        self._proposals.set_status(proposal_id, decision)
        self._activity.record(
            ActivityType.ACTION_ACCEPTED if decision == "accepted" else ActivityType.ACTION_REJECTED,
            object_id=str(proposal_id),
            details=proposal.action_type,
        )
        reviewed = self._proposals.get(proposal_id)
        if reviewed is None:
            raise RuntimeError("Reviewed Calendar proposal disappeared.")
        return reviewed

    def _proposal(self, proposal_id: int) -> ActionProposal:
        proposal = self._proposals.get(proposal_id)
        if proposal is None or proposal.action_type != self.CREATE_TRAVEL_EVENT:
            raise ValueError("Calendar event proposal was not found.")
        return proposal

    def _record(self, record_id: int) -> TravelRecord:
        record = next((item for item in self._records.list_travel_records() if item.id == record_id), None)
        if record is None:
            raise ValueError(f"Travel record {record_id} was not found.")
        return record

    @staticmethod
    def _validate_record(record: TravelRecord) -> None:
        if record.departure_time is None or record.arrival_time is None:
            raise ValueError("Travel records need departure and arrival times for Calendar.")
