"""Concrete personal records with provenance back to original sources."""
from __future__ import annotations
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

@dataclass(frozen=True, slots=True)
class TravelRecord:
    id: int | None
    source_id: int
    flight_number: str | None
    departure: str | None
    arrival: str | None
    departure_time: datetime | None
    arrival_time: datetime | None
    booking_reference: str | None

@dataclass(frozen=True, slots=True)
class TravelRecordProposal:
    record: TravelRecord
    field_evidence: dict[str, int]

class RecordService:
    def __init__(self, database_path: Path) -> None:
        self._database_path = database_path

    def create_travel_record(self, record: TravelRecord) -> TravelRecord:
        with sqlite3.connect(self._database_path) as connection:
            cursor = connection.execute(
                "INSERT INTO travel_records "
                "(source_id, flight_number, departure, arrival, departure_time, arrival_time, booking_reference) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                self._record_values(record),
            )
        return TravelRecord(
            int(cursor.lastrowid),
            record.source_id,
            record.flight_number,
            record.departure,
            record.arrival,
            record.departure_time,
            record.arrival_time,
            record.booking_reference,
        )

    def create_from_proposal(self, proposal: TravelRecordProposal) -> TravelRecord:
        """Persist an explicitly accepted proposal and its field-level evidence.

        Proposing is intentionally read-only. This separate method is the
        deterministic, transactional step that turns a proposal into a record.
        """
        if not proposal.field_evidence:
            raise ValueError("Cannot create a travel record without extracted, evidenced fields")

        with sqlite3.connect(self._database_path) as connection:
            connection.execute("PRAGMA foreign_keys = ON")
            cursor = connection.execute(
                "INSERT INTO travel_records "
                "(source_id, flight_number, departure, arrival, departure_time, arrival_time, booking_reference) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                self._record_values(proposal.record),
            )
            record_id = int(cursor.lastrowid)
            connection.executemany(
                "INSERT INTO travel_record_evidence (travel_record_id, field_name, fragment_id) VALUES (?, ?, ?)",
                [(record_id, field_name, fragment_id) for field_name, fragment_id in proposal.field_evidence.items()],
            )
        return TravelRecord(
            record_id,
            proposal.record.source_id,
            proposal.record.flight_number,
            proposal.record.departure,
            proposal.record.arrival,
            proposal.record.departure_time,
            proposal.record.arrival_time,
            proposal.record.booking_reference,
        )

    def add_field_evidence(self, record_id: int, field_name: str, fragment_id: int) -> None:
        with sqlite3.connect(self._database_path) as connection:
            connection.execute(
                "INSERT OR REPLACE INTO travel_record_evidence (travel_record_id, field_name, fragment_id) VALUES (?, ?, ?)",
                (record_id, field_name, fragment_id),
            )

    def list_travel_records(self) -> list[TravelRecord]:
        with sqlite3.connect(self._database_path) as connection:
            rows = connection.execute(
                "SELECT id, source_id, flight_number, departure, arrival, departure_time, arrival_time, booking_reference "
                "FROM travel_records ORDER BY id"
            ).fetchall()
        return [
            TravelRecord(
                int(row[0]), int(row[1]), str(row[2]) if row[2] else None,
                str(row[3]) if row[3] else None, str(row[4]) if row[4] else None,
                datetime.fromisoformat(str(row[5])) if row[5] else None,
                datetime.fromisoformat(str(row[6])) if row[6] else None,
                str(row[7]) if row[7] else None,
            )
            for row in rows
        ]

    def propose_travel_record(self, source_id: int, fragments: list[tuple[int, str]]) -> TravelRecordProposal:
        flight_number = departure = arrival = booking_reference = None
        departure_time = arrival_time = None
        evidence: dict[str, int] = {}
        for fragment_id, text in fragments:
            if flight_number is None and (match := re.search(r"\b[A-Z]{2}\d{2,4}\b", text)):
                flight_number = match.group(0); evidence["flight_number"] = fragment_id
            if departure is None and (match := re.search(r"Departure:\s*([^\n]+)", text, re.I)):
                departure = match.group(1).strip(); evidence["departure"] = fragment_id
            if arrival is None and (match := re.search(r"Arrival:\s*([^\n]+)", text, re.I)):
                arrival = match.group(1).strip(); evidence["arrival"] = fragment_id
            if booking_reference is None and (match := re.search(r"(?:Booking Reference|PNR):\s*([^\s]+)", text, re.I)):
                booking_reference = match.group(1); evidence["booking_reference"] = fragment_id
            if departure_time is None and (value := self._labeled_datetime("Departure Time", text)):
                departure_time = value; evidence["departure_time"] = fragment_id
            if arrival_time is None and (value := self._labeled_datetime("Arrival Time", text)):
                arrival_time = value; evidence["arrival_time"] = fragment_id
        return TravelRecordProposal(
            TravelRecord(None, source_id, flight_number, departure, arrival, departure_time, arrival_time, booking_reference),
            evidence,
        )

    @staticmethod
    def _record_values(record: TravelRecord) -> tuple[object, ...]:
        return (
            record.source_id, record.flight_number, record.departure, record.arrival,
            record.departure_time.isoformat() if record.departure_time else None,
            record.arrival_time.isoformat() if record.arrival_time else None,
            record.booking_reference,
        )

    @staticmethod
    def _labeled_datetime(label: str, text: str) -> datetime | None:
        match = re.search(rf"{label}:\s*([^\n]+)", text, re.IGNORECASE)
        if match is None:
            return None
        try:
            return datetime.fromisoformat(match.group(1).strip().replace("Z", "+00:00"))
        except ValueError:
            return None
