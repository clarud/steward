"""Concrete personal records with provenance back to original sources."""
from __future__ import annotations
import sqlite3
import re
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
    def __init__(self, database_path: Path) -> None: self._database_path=database_path
    def create_travel_record(self, record: TravelRecord) -> TravelRecord:
        with sqlite3.connect(self._database_path) as connection:
            cursor=connection.execute("INSERT INTO travel_records (source_id,flight_number,departure,arrival,departure_time,arrival_time,booking_reference) VALUES (?,?,?,?,?,?,?)",(record.source_id,record.flight_number,record.departure,record.arrival,record.departure_time.isoformat() if record.departure_time else None,record.arrival_time.isoformat() if record.arrival_time else None,record.booking_reference))
        return TravelRecord(int(cursor.lastrowid),record.source_id,record.flight_number,record.departure,record.arrival,record.departure_time,record.arrival_time,record.booking_reference)
    def add_field_evidence(self, record_id: int, field_name: str, fragment_id: int) -> None:
        with sqlite3.connect(self._database_path) as connection:
            connection.execute("INSERT OR REPLACE INTO travel_record_evidence (travel_record_id,field_name,fragment_id) VALUES (?,?,?)",(record_id,field_name,fragment_id))
    def list_travel_records(self) -> list[TravelRecord]:
        with sqlite3.connect(self._database_path) as connection:
            rows=connection.execute("SELECT id,source_id,flight_number,departure,arrival,departure_time,arrival_time,booking_reference FROM travel_records ORDER BY id").fetchall()
        return [TravelRecord(int(r[0]),int(r[1]),str(r[2]) if r[2] else None,str(r[3]) if r[3] else None,str(r[4]) if r[4] else None,datetime.fromisoformat(str(r[5])) if r[5] else None,datetime.fromisoformat(str(r[6])) if r[6] else None,str(r[7]) if r[7] else None) for r in rows]
    def propose_travel_record(self, source_id: int, fragments: list[tuple[int, str]]) -> TravelRecordProposal:
        flight_number = departure = arrival = booking_reference = None
        evidence: dict[str, int] = {}
        for fragment_id, text in fragments:
            if flight_number is None and (match := re.search(r"\b[A-Z]{2}\d{2,4}\b", text)):
                flight_number=match.group(0); evidence["flight_number"]=fragment_id
            if departure is None and (match := re.search(r"Departure:\s*([^\n]+)", text, re.I)):
                departure=match.group(1).strip(); evidence["departure"]=fragment_id
            if arrival is None and (match := re.search(r"Arrival:\s*([^\n]+)", text, re.I)):
                arrival=match.group(1).strip(); evidence["arrival"]=fragment_id
            if booking_reference is None and (match := re.search(r"(?:Booking Reference|PNR):\s*([^\s]+)", text, re.I)):
                booking_reference=match.group(1); evidence["booking_reference"]=fragment_id
        return TravelRecordProposal(TravelRecord(None,source_id,flight_number,departure,arrival,None,None,booking_reference),evidence)
