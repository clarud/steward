"""Google Calendar OAuth and read-only service boundary."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol
import sqlite3

from steward.activity import ActivityService, ActivityType
from steward.action_proposals import ActionProposal, ActionProposalRepository
from steward.records import TravelRecord
from steward.records import RecordService
from steward.tasks import Task, TaskService


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
    location: str | None = None
    description: str | None = None


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
            location=str(item["location"]) if item.get("location") else None,
            description=str(item["description"]) if item.get("description") else None,
        )


class CalendarLinkRepository:
    """Persist narrow local links to externally authoritative Calendar events."""

    def __init__(self, database_path: Path) -> None:
        self._database_path = database_path

    def travel_event_id(self, record_id: int) -> str | None:
        return self._event_id("calendar_event_links", "travel_record_id", record_id)

    def travel_record_id_for_event(self, event_id: str) -> int | None:
        """Return the local travel record for a Steward-created event.

        This is only a lookup of Steward's own opaque link. It does not infer
        that an arbitrary Google Calendar event is a travel record or read a
        stale copy of the event from SQLite.
        """

        if not event_id.strip():
            raise ValueError("Calendar event ID must not be empty.")
        with sqlite3.connect(self._database_path) as connection:
            row = connection.execute(
                "SELECT travel_record_id FROM calendar_event_links WHERE external_event_id = ?",
                (event_id,),
            ).fetchone()
        return int(row[0]) if row else None

    def task_event_id(self, task_id: int) -> str | None:
        return self._event_id("calendar_task_event_links", "task_id", task_id)

    def task_id_for_event(self, event_id: str) -> int | None:
        """Return Steward's optional local task link for one opaque Calendar ID.

        This is deliberately a local relationship lookup, not a Calendar
        search. Google Calendar remains authoritative for the event itself;
        the link merely lets a user move from an event Steward created as a
        reviewed deadline marker back to its independent task.
        """

        if not event_id.strip():
            raise ValueError("Calendar event ID must not be empty.")
        with sqlite3.connect(self._database_path) as connection:
            rows = connection.execute(
                "SELECT task_id FROM calendar_task_event_links WHERE external_event_id = ?",
                (event_id,),
            ).fetchall()
        if len(rows) > 1:
            raise ValueError("Calendar event is linked to multiple Steward tasks.")
        return int(rows[0][0]) if rows else None

    def associated_event_id_for_task(self, task_id: int) -> str | None:
        """Return an explicitly reviewed existing-event association, if any."""

        if task_id <= 0:
            raise ValueError("Task ID must be positive.")
        with sqlite3.connect(self._database_path) as connection:
            row = connection.execute(
                "SELECT external_event_id FROM task_calendar_associations WHERE task_id = ?", (task_id,)
            ).fetchone()
        return str(row[0]) if row else None

    def associated_task_id_for_event(self, event_id: str) -> int | None:
        if not event_id.strip():
            raise ValueError("Calendar event ID must not be empty.")
        with sqlite3.connect(self._database_path) as connection:
            row = connection.execute(
                "SELECT task_id FROM task_calendar_associations WHERE external_event_id = ?", (event_id,)
            ).fetchone()
        return int(row[0]) if row else None

    def associate_existing_event(self, task_id: int, event_id: str) -> bool:
        """Create one local 1:1 association without changing Calendar."""

        if task_id <= 0 or not event_id.strip():
            raise ValueError("An association requires a positive task ID and Calendar event ID.")
        with sqlite3.connect(self._database_path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing_task = connection.execute(
                "SELECT external_event_id FROM task_calendar_associations WHERE task_id = ?", (task_id,)
            ).fetchone()
            existing_event = connection.execute(
                "SELECT task_id FROM task_calendar_associations WHERE external_event_id = ?", (event_id,)
            ).fetchone()
            if existing_task is not None or existing_event is not None:
                if existing_task is not None and str(existing_task[0]) == event_id:
                    return False
                raise ValueError("The task or Calendar event already has an explicit association.")
            connection.execute(
                "INSERT INTO task_calendar_associations (task_id, external_event_id, created_at) VALUES (?, ?, ?)",
                (task_id, event_id, datetime.now(UTC).isoformat()),
            )
        return True

    def remove_existing_event_association(self, task_id: int, event_id: str) -> bool:
        """Remove one local relationship without changing the Calendar event.

        The event ID makes a stale proposal fail closed if the task was linked
        to a different event after this review was created.
        """

        if task_id <= 0 or not event_id.strip():
            raise ValueError("Removing an association requires a positive task ID and Calendar event ID.")
        with sqlite3.connect(self._database_path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT external_event_id FROM task_calendar_associations WHERE task_id = ?", (task_id,)
            ).fetchone()
            if row is None:
                return False
            if str(row[0]) != event_id:
                raise ValueError("The task is now associated with a different Calendar event.")
            connection.execute(
                "DELETE FROM task_calendar_associations WHERE task_id = ? AND external_event_id = ?",
                (task_id, event_id),
            )
        return True

    def link_travel_event(self, key: str, record_id: int, event_id: str) -> bool:
        return self._link("calendar_event_links", "travel_record_id", key, record_id, event_id)

    def link_task_event(self, key: str, task_id: int, event_id: str) -> bool:
        return self._link("calendar_task_event_links", "task_id", key, task_id, event_id)

    def _event_id(self, table: str, owner_column: str, owner_id: int) -> str | None:
        if owner_id <= 0:
            raise ValueError("Calendar link owner ID must be positive.")
        with sqlite3.connect(self._database_path) as connection:
            row = connection.execute(
                f"SELECT external_event_id FROM {table} WHERE {owner_column} = ?",
                (owner_id,),
            ).fetchone()
        return str(row[0]) if row else None

    def _link(
        self, table: str, owner_column: str, key: str, owner_id: int, event_id: str
    ) -> bool:
        if owner_id <= 0 or not key.strip() or not event_id.strip():
            raise ValueError("Calendar links require a key, positive owner ID, and event ID.")
        with sqlite3.connect(self._database_path) as connection:
            connection.execute("PRAGMA foreign_keys = ON")
            cursor = connection.execute(
                f"INSERT OR IGNORE INTO {table} "
                f"(idempotency_key, {owner_column}, external_event_id, created_at) "
                "VALUES (?, ?, ?, ?)",
                (key, owner_id, event_id, datetime.now(UTC).isoformat()),
            )
        return bool(cursor.rowcount)


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
    from google.auth.exceptions import RefreshError
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build

    credentials = None
    if token_path.is_file():
        credentials = Credentials.from_authorized_user_file(str(token_path), scopes)
        if credentials and not credentials.has_scopes(scopes):
            credentials = None
    if credentials and credentials.expired and credentials.refresh_token:
        try:
            credentials.refresh(Request())
        except RefreshError:
            # A revoked refresh token cannot be repaired locally. Fall through
            # to browser consent instead of leaking a provider traceback.
            credentials = None
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
        self._links = CalendarLinkRepository(database_path)

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

    def create_task_deadline_event(self, task: Task) -> CalendarEvent:
        """Create one reviewed, idempotent Calendar marker for a precise task deadline."""

        if task.id is None:
            raise ValueError("Only persisted tasks can create Calendar events.")
        if task.due_at is None:
            raise ValueError("Tasks need an explicit timezone-aware deadline for Calendar.")
        if task.due_at.tzinfo is None or task.due_at.utcoffset() is None:
            raise ValueError("Task deadlines must include a timezone for Calendar.")
        key = f"task:{task.id}"
        existing = self._existing_task_event(key)
        if existing is not None:
            return self._calendar.get_event(existing)
        recovered = self._calendar.find_by_idempotency_key(key)
        if recovered is not None:
            self._link_task_event(key, task.id, recovered.id)
            return recovered
        start = task.due_at.astimezone(UTC)
        event = self._calendar.create_event(
            summary=f"Due: {task.title}",
            start=start,
            end=start + timedelta(minutes=15),
            description=f"Steward task {task.id}. This is a reviewed deadline marker.",
            idempotency_key=key,
        )
        self._link_task_event(key, task.id, event.id)
        return event

    def _link(self, key: str, record_id: int, event_id: str) -> None:
        if self._links.link_travel_event(key, record_id, event_id):
            self._activity.record(ActivityType.CALENDAR_EVENT_CREATED, object_id=event_id, details=key)

    def _existing(self, key: str) -> str | None:
        record_id = int(key.removeprefix("travel-record:"))
        return self._links.travel_event_id(record_id)

    def _link_task_event(self, key: str, task_id: int, event_id: str) -> None:
        if self._links.link_task_event(key, task_id, event_id):
            self._activity.record(ActivityType.CALENDAR_EVENT_CREATED, object_id=event_id, details=key)

    def _existing_task_event(self, key: str) -> str | None:
        task_id = int(key.removeprefix("task:"))
        return self._links.task_event_id(task_id)


class CalendarEventProposalService:
    """Persist Calendar requests before an explicit approval can create an event."""

    CREATE_TRAVEL_EVENT = "create_calendar_travel_event"
    CREATE_TASK_EVENT = "create_calendar_task_event"

    def __init__(
        self,
        proposals: ActionProposalRepository,
        records: RecordService,
        activity: ActivityService,
        tasks: TaskService | None = None,
    ) -> None:
        self._proposals = proposals
        self._records = records
        self._activity = activity
        self._tasks = tasks

    def propose_travel_event(self, record_id: int) -> ActionProposal:
        record = self._record(record_id)
        self._validate_record(record)
        payload = {"record_id": str(record_id), "snapshot": self._snapshot(record)}
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

    def propose_task_event(self, task_id: int) -> ActionProposal:
        task = self._task(task_id)
        self._validate_task(task)
        payload = {"task_id": str(task_id), "snapshot": self._snapshot(task)}
        existing = self._proposals.find_pending(self.CREATE_TASK_EVENT, payload)
        if existing is not None:
            return existing
        proposal = self._proposals.add(self.CREATE_TASK_EVENT, payload)
        self._activity.record(
            ActivityType.ACTION_PROPOSED,
            object_id=str(proposal.id),
            details=f"Create Calendar deadline event for task {task_id}",
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
            if proposal.action_type == self.CREATE_TRAVEL_EVENT:
                record = self._record(int(proposal.payload["record_id"]))
                self._validate_record(record)
                if proposal.payload.get("snapshot") != self._snapshot(record):
                    raise ValueError("Calendar preview is stale. Request a fresh Calendar proposal before approving.")
                calendar_writer.create_travel_event(record)
            else:
                task = self._task(int(proposal.payload["task_id"]))
                self._validate_task(task)
                if proposal.payload.get("snapshot") != self._snapshot(task):
                    raise ValueError("Calendar preview is stale. Request a fresh Calendar proposal before approving.")
                calendar_writer.create_task_deadline_event(task)
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

    @staticmethod
    def _snapshot(item: TravelRecord | Task) -> str:
        return json.dumps(asdict(item), sort_keys=True, default=lambda value: value.isoformat())

    def _proposal(self, proposal_id: int) -> ActionProposal:
        proposal = self._proposals.get(proposal_id)
        if proposal is None or proposal.action_type not in {self.CREATE_TRAVEL_EVENT, self.CREATE_TASK_EVENT}:
            raise ValueError("Calendar event proposal was not found.")
        return proposal

    def _record(self, record_id: int) -> TravelRecord:
        record = next((item for item in self._records.list_travel_records() if item.id == record_id), None)
        if record is None:
            raise ValueError(f"Travel record {record_id} was not found.")
        return record

    def _task(self, task_id: int) -> Task:
        if self._tasks is None:
            raise ValueError("Task Calendar proposals are not configured for this Steward process.")
        task = self._tasks.get(task_id)
        if task is None:
            raise ValueError(f"Task {task_id} was not found.")
        return task

    @staticmethod
    def _validate_record(record: TravelRecord) -> None:
        if record.departure_time is None or record.arrival_time is None:
            raise ValueError("Travel records need departure and arrival times for Calendar.")
        if record.departure_time.tzinfo is None or record.arrival_time.tzinfo is None:
            raise ValueError("Travel record times must include a timezone for Calendar.")
        if record.departure_time >= record.arrival_time:
            raise ValueError("Travel record departure must be before arrival for Calendar.")

    @staticmethod
    def _validate_task(task: Task) -> None:
        if task.due_at is None:
            raise ValueError("Tasks need an explicit timezone-aware deadline for Calendar.")
        if task.due_at.tzinfo is None or task.due_at.utcoffset() is None:
            raise ValueError("Task deadlines must include a timezone for Calendar.")
