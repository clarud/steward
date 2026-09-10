"""Concrete personal records with provenance back to original sources."""
from __future__ import annotations
import re
import json
from hashlib import sha256
import sqlite3
from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation
from datetime import UTC, datetime
from pathlib import Path
from steward.activity import ActivityType

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


@dataclass(frozen=True, slots=True)
class TravelRecordReference:
    """An additional concrete identifier supported by a source fragment."""

    id: int | None
    travel_record_id: int
    reference_type: str
    value: str
    fragment_id: int

@dataclass(frozen=True, slots=True)
class ReceiptRecord:
    id: int | None; source_id: int; merchant: str | None; total_cents: int | None
    currency: str | None; purchased_at: datetime | None; receipt_number: str | None

@dataclass(frozen=True, slots=True)
class ReceiptRecordProposal:
    record: ReceiptRecord; field_evidence: dict[str, int]


@dataclass(frozen=True, slots=True)
class WarrantyRecord:
    id: int | None
    source_id: int
    product_name: str | None
    provider: str | None
    warranty_number: str | None
    coverage_ends_at: datetime | None


@dataclass(frozen=True, slots=True)
class WarrantyRecordProposal:
    record: WarrantyRecord
    field_evidence: dict[str, int]

def record_review_snapshot(
    proposal: TravelRecordProposal | ReceiptRecordProposal | WarrantyRecordProposal,
    fragments: list[tuple[int, str]],
) -> str:
    """Persist reviewed values and bind them to the exact extraction evidence.

    Include text digests because fragment IDs may be reused during re-extraction.
    Stable serialization also detects changes in extraction behavior on upgrade.
    """
    return json.dumps(
        {
            "proposal": asdict(proposal),
            "evidence": [(identifier, sha256(text.encode("utf-8")).hexdigest()) for identifier, text in fragments],
        },
        sort_keys=True,
        default=lambda value: value.isoformat(),
    )


class RecordService:
    def __init__(self, database_path: Path) -> None:
        self._database_path = database_path

    @staticmethod
    def _validate_review_snapshot(connection: sqlite3.Connection, proposal: TravelRecordProposal | ReceiptRecordProposal | WarrantyRecordProposal, expected_snapshot: str | None) -> None:
        """Validate against current evidence while holding the record write lock."""
        if expected_snapshot is None:
            return
        connection.execute("BEGIN IMMEDIATE")
        source = connection.execute("SELECT status FROM sources WHERE id = ?", (proposal.record.source_id,)).fetchone()
        if source is None or source[0] != "active":
            raise ValueError("The record source is no longer active. Request a new preview after restoring it.")
        fragments = connection.execute(
            "SELECT id, text FROM source_fragments WHERE source_id = ? ORDER BY ordinal",
            (proposal.record.source_id,),
        ).fetchall()
        if record_review_snapshot(proposal, fragments) != expected_snapshot:
            raise ValueError("This record preview is stale. Request a new record proposal before approving.")

    @staticmethod
    def _accept_record_action(connection: sqlite3.Connection, action_id: int | None, action_type: str, source_id: int, snapshot: str | None, record_id: int) -> None:
        """Commit approval and audit with the record, or roll the entire write back."""
        if action_id is None:
            return
        row = connection.execute(
            "SELECT action_type, payload_json, status FROM action_proposals WHERE id = ?", (action_id,),
        ).fetchone()
        if row is None or row[2] != "pending":
            raise ValueError("Action proposal was not found or was already reviewed.")
        payload = json.loads(row[1])
        if snapshot is None or row[0] != action_type or payload.get("snapshot") != snapshot or payload.get("source_id") != str(source_id):
            raise ValueError("Record approval does not match the reviewed proposal.")
        now = datetime.now(UTC).isoformat()
        connection.execute("UPDATE action_proposals SET status = 'accepted', reviewed_at = ? WHERE id = ?", (now, action_id))
        connection.execute(
            "INSERT INTO activity_events (event_type, object_id, details, occurred_at) VALUES (?, ?, ?, ?)",
            (ActivityType.ACTION_ACCEPTED.value, str(action_id), f"{action_type}: record {record_id}", now),
        )

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

    def field_evidence(self, record_type: str, record_id: int) -> dict[str, int]:
        """Return field-to-fragment provenance for one persisted record.

        The table name is selected from a fixed domain mapping rather than
        supplied by a caller, so this read-only convenience method cannot turn
        a record type from a transport request into SQL.
        """

        table, identifier_column = {
            "travel": ("travel_record_evidence", "travel_record_id"),
            "receipt": ("receipt_record_evidence", "receipt_record_id"),
            "warranty": ("warranty_record_evidence", "warranty_record_id"),
        }.get(record_type, (None, None))
        if table is None or identifier_column is None:
            raise ValueError("Record type must be travel, receipt, or warranty.")
        with sqlite3.connect(self._database_path) as connection:
            rows = connection.execute(
                f"SELECT field_name, fragment_id FROM {table} WHERE {identifier_column} = ?",
                (record_id,),
            ).fetchall()
        return {str(field_name): int(fragment_id) for field_name, fragment_id in rows}

    def correct_travel_field(self, record_id: int, field: str, value: str) -> TravelRecord:
        """Apply one explicit user correction without rewriting source evidence."""
        record = next((item for item in self.list_travel_records() if item.id == record_id), None)
        if record is None:
            raise ValueError(f"Travel record {record_id} was not found.")
        normalized = self._normalized_travel_field(field, value)
        stored = normalized.isoformat() if isinstance(normalized, datetime) else normalized
        with sqlite3.connect(self._database_path) as connection:
            connection.execute(f"UPDATE travel_records SET {field} = ? WHERE id = ?", (stored, record_id))
        values = {
            "flight_number": record.flight_number, "departure": record.departure, "arrival": record.arrival,
            "departure_time": record.departure_time, "arrival_time": record.arrival_time,
            "booking_reference": record.booking_reference,
        }
        values[field] = normalized
        return TravelRecord(record.id, record.source_id, **values)

    def validate_travel_field(self, field: str, value: str) -> None:
        self._normalized_travel_field(field, value)

    def correct_receipt_field(self, record_id: int, field: str, value: str) -> ReceiptRecord:
        """Apply one explicit correction to a receipt without altering its source."""
        record = next((item for item in self.list_receipt_records() if item.id == record_id), None)
        if record is None:
            raise ValueError(f"Receipt record {record_id} was not found.")
        normalized = self._normalized_receipt_field(field, value)
        stored = normalized.isoformat() if isinstance(normalized, datetime) else normalized
        with sqlite3.connect(self._database_path) as connection:
            connection.execute(f"UPDATE receipt_records SET {field} = ? WHERE id = ?", (stored, record_id))
        values = {
            "merchant": record.merchant, "total_cents": record.total_cents, "currency": record.currency,
            "purchased_at": record.purchased_at, "receipt_number": record.receipt_number,
        }
        values[field] = normalized
        return ReceiptRecord(record.id, record.source_id, **values)

    def validate_receipt_field(self, field: str, value: str) -> None:
        self._normalized_receipt_field(field, value)

    def correct_warranty_field(self, record_id: int, field: str, value: str) -> WarrantyRecord:
        """Apply one explicit correction to a warranty without altering its source."""
        record = next((item for item in self.list_warranty_records() if item.id == record_id), None)
        if record is None:
            raise ValueError(f"Warranty record {record_id} was not found.")
        normalized = self._normalized_warranty_field(field, value)
        stored = normalized.isoformat() if isinstance(normalized, datetime) else normalized
        with sqlite3.connect(self._database_path) as connection:
            connection.execute(f"UPDATE warranty_records SET {field} = ? WHERE id = ?", (stored, record_id))
        values = {
            "product_name": record.product_name, "provider": record.provider,
            "warranty_number": record.warranty_number, "coverage_ends_at": record.coverage_ends_at,
        }
        values[field] = normalized
        return WarrantyRecord(record.id, record.source_id, **values)

    def validate_warranty_field(self, field: str, value: str) -> None:
        self._normalized_warranty_field(field, value)

    @staticmethod
    def _normalized_travel_field(field: str, value: str) -> object | None:
        allowed = {"flight_number", "departure", "arrival", "departure_time", "arrival_time", "booking_reference"}
        if field not in allowed:
            raise ValueError("Travel field must be flight_number, departure, arrival, departure_time, arrival_time, or booking_reference.")
        normalized: object | None = " ".join(value.split()) or None
        if field in {"departure_time", "arrival_time"} and normalized is not None:
            try:
                normalized = datetime.fromisoformat(str(normalized))
            except ValueError as error:
                raise ValueError(f"{field} must be ISO-8601 with a timezone offset.") from error
            if normalized.tzinfo is None:
                raise ValueError(f"{field} must be ISO-8601 with a timezone offset.")
        return normalized

    @staticmethod
    def _normalized_receipt_field(field: str, value: str) -> object | None:
        if field not in {"merchant", "total_cents", "currency", "purchased_at", "receipt_number"}:
            raise ValueError("Receipt field must be merchant, total_cents, currency, purchased_at, or receipt_number.")
        normalized = " ".join(value.split())
        if not normalized:
            return None
        if field == "total_cents":
            try:
                amount = Decimal(normalized)
            except InvalidOperation as error:
                raise ValueError("total_cents must be a decimal amount such as 12.50.") from error
            if amount < 0 or amount.as_tuple().exponent < -2:
                raise ValueError("total_cents must be a non-negative amount with at most two decimal places.")
            return int(amount * 100)
        if field == "currency":
            currency = normalized.upper()
            if not re.fullmatch(r"[A-Z]{3}", currency):
                raise ValueError("currency must be a three-letter code such as SGD.")
            return currency
        if field == "purchased_at":
            try:
                timestamp = datetime.fromisoformat(normalized)
            except ValueError as error:
                raise ValueError("purchased_at must be ISO-8601 with a timezone offset.") from error
            if timestamp.tzinfo is None:
                raise ValueError("purchased_at must be ISO-8601 with a timezone offset.")
            return timestamp
        return normalized

    @staticmethod
    def _normalized_warranty_field(field: str, value: str) -> object | None:
        if field not in {"product_name", "provider", "warranty_number", "coverage_ends_at"}:
            raise ValueError("Warranty field must be product_name, provider, warranty_number, or coverage_ends_at.")
        normalized = " ".join(value.split())
        if not normalized:
            return None
        if field == "coverage_ends_at":
            try:
                timestamp = datetime.fromisoformat(normalized)
            except ValueError as error:
                raise ValueError("coverage_ends_at must be ISO-8601 with a timezone offset.") from error
            if timestamp.tzinfo is None:
                raise ValueError("coverage_ends_at must be ISO-8601 with a timezone offset.")
            return timestamp
        return normalized

    def propose_receipt_record(self, source_id: int, fragments: list[tuple[int, str]]) -> ReceiptRecordProposal:
        merchant = currency = receipt_number = None; total_cents = None; purchased_at = None; evidence: dict[str, int] = {}
        for fragment_id, text in fragments:
            if merchant is None and (match := re.search(r"(?:Merchant|Store):\s*([^\n]+)", text, re.I)):
                merchant = match.group(1).strip(); evidence["merchant"] = fragment_id
            if total_cents is None and (match := re.search(r"(?:Total|Amount Paid):\s*([A-Z]{3})?\s*[$]?\s*(\d+(?:\.\d{2})?)", text, re.I)):
                currency = match.group(1) or "USD"; total_cents = round(float(match.group(2)) * 100); evidence["total_cents"] = fragment_id; evidence["currency"] = fragment_id
            if receipt_number is None and (match := re.search(r"(?:Receipt|Invoice)(?: Number| No\.?| #)?:\s*([^\s]+)", text, re.I)):
                receipt_number = match.group(1); evidence["receipt_number"] = fragment_id
            if purchased_at is None and (value := self._labeled_datetime("Purchase Date", text)):
                purchased_at = value; evidence["purchased_at"] = fragment_id
        return ReceiptRecordProposal(ReceiptRecord(None, source_id, merchant, total_cents, currency, purchased_at, receipt_number), evidence)

    def create_receipt_from_proposal(self, proposal: ReceiptRecordProposal, *, expected_snapshot: str | None = None, action_id: int | None = None) -> ReceiptRecord:
        if not proposal.field_evidence:
            raise ValueError("Cannot create a receipt record without extracted, evidenced fields")
        record = proposal.record
        with sqlite3.connect(self._database_path) as connection:
            connection.execute("PRAGMA foreign_keys = ON")
            self._validate_review_snapshot(connection, proposal, expected_snapshot)
            cursor = connection.execute("INSERT INTO receipt_records (source_id, merchant, total_cents, currency, purchased_at, receipt_number) VALUES (?, ?, ?, ?, ?, ?)", (record.source_id, record.merchant, record.total_cents, record.currency, record.purchased_at.isoformat() if record.purchased_at else None, record.receipt_number))
            record_id = int(cursor.lastrowid)
            connection.executemany("INSERT INTO receipt_record_evidence (receipt_record_id, field_name, fragment_id) VALUES (?, ?, ?)", [(record_id, field, fragment) for field, fragment in proposal.field_evidence.items()])
            self._accept_record_action(connection, action_id, "create_receipt_record", record.source_id, expected_snapshot, record_id)
        return ReceiptRecord(record_id, record.source_id, record.merchant, record.total_cents, record.currency, record.purchased_at, record.receipt_number)

    def list_receipt_records(self) -> list[ReceiptRecord]:
        with sqlite3.connect(self._database_path) as connection:
            rows = connection.execute("SELECT id, source_id, merchant, total_cents, currency, purchased_at, receipt_number FROM receipt_records ORDER BY id").fetchall()
        return [ReceiptRecord(int(row[0]), int(row[1]), str(row[2]) if row[2] else None, int(row[3]) if row[3] is not None else None, str(row[4]) if row[4] else None, datetime.fromisoformat(str(row[5])) if row[5] else None, str(row[6]) if row[6] else None) for row in rows]

    def propose_warranty_record(self, source_id: int, fragments: list[tuple[int, str]]) -> WarrantyRecordProposal:
        product = provider = warranty_number = None
        coverage_ends_at = None
        evidence: dict[str, int] = {}
        for fragment_id, text in fragments:
            if product is None and (match := re.search(r"(?:Product|Item):\s*([^\n]+)", text, re.I)):
                product = match.group(1).strip(); evidence["product_name"] = fragment_id
            if provider is None and (match := re.search(r"(?:Provider|Manufacturer):\s*([^\n]+)", text, re.I)):
                provider = match.group(1).strip(); evidence["provider"] = fragment_id
            if warranty_number is None and (match := re.search(r"(?:Warranty|Contract)(?: Number| No\.?| #)?:\s*([^\s]+)", text, re.I)):
                warranty_number = match.group(1); evidence["warranty_number"] = fragment_id
            if coverage_ends_at is None and (value := self._labeled_datetime("Coverage Ends", text)):
                coverage_ends_at = value; evidence["coverage_ends_at"] = fragment_id
        return WarrantyRecordProposal(WarrantyRecord(None, source_id, product, provider, warranty_number, coverage_ends_at), evidence)

    def create_warranty_from_proposal(self, proposal: WarrantyRecordProposal, *, expected_snapshot: str | None = None, action_id: int | None = None) -> WarrantyRecord:
        if not proposal.field_evidence:
            raise ValueError("Cannot create a warranty record without extracted, evidenced fields")
        record = proposal.record
        with sqlite3.connect(self._database_path) as connection:
            connection.execute("PRAGMA foreign_keys = ON")
            self._validate_review_snapshot(connection, proposal, expected_snapshot)
            cursor = connection.execute("INSERT INTO warranty_records (source_id, product_name, provider, warranty_number, coverage_ends_at) VALUES (?, ?, ?, ?, ?)", (record.source_id, record.product_name, record.provider, record.warranty_number, record.coverage_ends_at.isoformat() if record.coverage_ends_at else None))
            record_id = int(cursor.lastrowid)
            connection.executemany("INSERT INTO warranty_record_evidence (warranty_record_id, field_name, fragment_id) VALUES (?, ?, ?)", [(record_id, field, fragment) for field, fragment in proposal.field_evidence.items()])
            self._accept_record_action(connection, action_id, "create_warranty_record", record.source_id, expected_snapshot, record_id)
        return WarrantyRecord(record_id, record.source_id, record.product_name, record.provider, record.warranty_number, record.coverage_ends_at)

    def list_warranty_records(self) -> list[WarrantyRecord]:
        with sqlite3.connect(self._database_path) as connection:
            rows = connection.execute("SELECT id, source_id, product_name, provider, warranty_number, coverage_ends_at FROM warranty_records ORDER BY id").fetchall()
        return [WarrantyRecord(int(row[0]), int(row[1]), str(row[2]) if row[2] else None, str(row[3]) if row[3] else None, str(row[4]) if row[4] else None, datetime.fromisoformat(str(row[5])) if row[5] else None) for row in rows]

    def create_from_proposal(self, proposal: TravelRecordProposal, *, expected_snapshot: str | None = None, action_id: int | None = None) -> TravelRecord:
        """Persist an explicitly accepted proposal and its field-level evidence.

        Proposing is intentionally read-only. This separate method is the
        deterministic, transactional step that turns a proposal into a record.
        """
        if not proposal.field_evidence:
            raise ValueError("Cannot create a travel record without extracted, evidenced fields")

        with sqlite3.connect(self._database_path) as connection:
            connection.execute("PRAGMA foreign_keys = ON")
            self._validate_review_snapshot(connection, proposal, expected_snapshot)
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
            self._accept_record_action(connection, action_id, "create_travel_record", proposal.record.source_id, expected_snapshot, record_id)
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

    def add_reference(
        self, record_id: int, reference_type: str, value: str, fragment_id: int
    ) -> TravelRecordReference:
        """Attach a typed, source-backed identifier to a persisted travel record."""

        normalized_type = "_".join(reference_type.casefold().split())
        normalized_value = value.strip()
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", normalized_type):
            raise ValueError("Reference type must use lowercase letters, numbers, or underscores.")
        if not normalized_value:
            raise ValueError("Reference value must not be empty.")
        with sqlite3.connect(self._database_path) as connection:
            connection.execute("PRAGMA foreign_keys = ON")
            record_row = connection.execute(
                "SELECT source_id FROM travel_records WHERE id = ?", (record_id,)
            ).fetchone()
            if record_row is None:
                raise ValueError(f"Travel record {record_id} was not found.")
            fragment_row = connection.execute(
                "SELECT source_id, text FROM source_fragments WHERE id = ?", (fragment_id,)
            ).fetchone()
            if fragment_row is None:
                raise ValueError(f"Fragment {fragment_id} was not found.")
            if int(fragment_row[0]) != int(record_row[0]):
                raise ValueError("Reference evidence must belong to the travel record's source.")
            if normalized_value.casefold() not in str(fragment_row[1]).casefold():
                raise ValueError("Reference value must appear in its supporting fragment.")
            cursor = connection.execute(
                "INSERT OR IGNORE INTO travel_record_references "
                "(travel_record_id, reference_type, value, fragment_id) VALUES (?, ?, ?, ?)",
                (record_id, normalized_type, normalized_value, fragment_id),
            )
            if cursor.rowcount == 1:
                if cursor.lastrowid is None:
                    raise RuntimeError("SQLite did not assign a travel record reference ID.")
                reference_id = int(cursor.lastrowid)
            else:
                row = connection.execute(
                    "SELECT id, fragment_id FROM travel_record_references "
                    "WHERE travel_record_id = ? AND reference_type = ? AND value = ?",
                    (record_id, normalized_type, normalized_value),
                ).fetchone()
                if row is None:
                    raise RuntimeError("Travel record reference was not persisted.")
                return TravelRecordReference(int(row[0]), record_id, normalized_type, normalized_value, int(row[1]))
        return TravelRecordReference(reference_id, record_id, normalized_type, normalized_value, fragment_id)

    def get_travel_record(self, record_id: int) -> TravelRecord | None:
        """Return one travel record without exposing the database to callers."""
        with sqlite3.connect(self._database_path) as connection:
            row = connection.execute(
                "SELECT id, source_id, flight_number, departure, arrival, departure_time, arrival_time, booking_reference "
                "FROM travel_records WHERE id = ?",
                (record_id,),
            ).fetchone()
        if row is None:
            return None
        return TravelRecord(
            int(row[0]), int(row[1]), str(row[2]) if row[2] else None,
            str(row[3]) if row[3] else None, str(row[4]) if row[4] else None,
            datetime.fromisoformat(str(row[5])) if row[5] else None,
            datetime.fromisoformat(str(row[6])) if row[6] else None,
            str(row[7]) if row[7] else None,
        )

    def list_references(self, record_id: int) -> tuple[TravelRecordReference, ...]:
        with sqlite3.connect(self._database_path) as connection:
            rows = connection.execute(
                "SELECT id, travel_record_id, reference_type, value, fragment_id "
                "FROM travel_record_references WHERE travel_record_id = ? ORDER BY id",
                (record_id,),
            ).fetchall()
        return tuple(
            TravelRecordReference(int(row[0]), int(row[1]), str(row[2]), str(row[3]), int(row[4]))
            for row in rows
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
