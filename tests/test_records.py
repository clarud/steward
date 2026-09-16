from datetime import UTC, datetime
from pathlib import Path
import sqlite3
import pytest
from steward.records import RecordService, TravelRecord, record_review_snapshot
from steward.records import ReceiptRecord, ReceiptRecordProposal, WarrantyRecord, WarrantyRecordProposal
from steward.sources import Source, SourceRepository, SourceType
from steward.storage import initialize_database
from steward.extraction import ExtractionResult, SourceFragment, SourceFragmentRepository
from steward.action_proposals import ActionProposalRepository
from steward.activity import ActivityService, ActivityType


@pytest.mark.parametrize("kind,text", [
    ("travel", "Flight SQ638"), ("receipt", "Merchant: Cafe"), ("warranty", "Product: Laptop"),
])
def test_record_approval_rolls_back_on_failure_and_cannot_execute_twice(tmp_path: Path, kind: str, text: str) -> None:
    database = tmp_path / "db.sqlite"
    initialize_database(database)
    now = datetime(2026, 9, 8, tzinfo=UTC)
    source = SourceRepository(database).add(Source(None, tmp_path / "source.md", "a" * 64, SourceType.MARKDOWN, 0, now, now, now))
    stored = SourceFragmentRepository(database).replace_for_source(ExtractionResult(source.id, (
        SourceFragment(None, source.id, None, 0, text, "line 1"),
    )))
    parts = [(part.id, part.text) for part in stored]
    service = RecordService(database)
    proposal = getattr(service, f"propose_{kind}_record")(source.id, parts)
    snapshot = record_review_snapshot(proposal, parts)
    actions = ActionProposalRepository(database)
    action = actions.add(f"create_{kind}_record", {"source_id": str(source.id), "snapshot": snapshot})
    create = service.create_from_proposal if kind == "travel" else getattr(service, f"create_{kind}_from_proposal")
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TRIGGER fail_audit BEFORE INSERT ON activity_events BEGIN SELECT RAISE(ABORT, 'injected failure'); END")
    with pytest.raises(sqlite3.IntegrityError, match="injected failure"):
        create(proposal, expected_snapshot=snapshot, action_id=action.id)
    assert actions.get(action.id).status == "pending"
    assert getattr(service, f"list_{kind}_records")() == []
    with sqlite3.connect(database) as connection:
        connection.execute("DROP TRIGGER fail_audit")
    create(proposal, expected_snapshot=snapshot, action_id=action.id)
    assert actions.get(action.id).status == "accepted"
    with pytest.raises(ValueError, match="already reviewed"):
        create(proposal, expected_snapshot=snapshot, action_id=action.id)
    assert len(getattr(service, f"list_{kind}_records")()) == 1
    events = ActivityService(database).list_recent()
    assert len(events) == 1 and events[0].event_type is ActivityType.ACTION_ACCEPTED

@pytest.mark.parametrize("kind,text", [
    ("travel", "Flight SQ638"),
    ("receipt", "Merchant: Cafe"),
    ("warranty", "Product: Laptop"),
])
def test_snapshot_validation_holds_write_lock_through_record_insert(tmp_path: Path, kind: str, text: str) -> None:
    database = tmp_path / "db.sqlite"
    initialize_database(database)
    now = datetime(2026, 9, 8, tzinfo=UTC)
    source = SourceRepository(database).add(Source(None, tmp_path / "source.md", "a" * 64, SourceType.MARKDOWN, 0, now, now, now))
    fragments = SourceFragmentRepository(database)
    stored = fragments.replace_for_source(ExtractionResult(source.id, (
        SourceFragment(None, source.id, None, 0, text, "line 1"),
    )))
    parts = [(part.id, part.text) for part in stored]

    class ContendedRecords(RecordService):
        @staticmethod
        def _validate_review_snapshot(connection, proposal, expected_snapshot):
            RecordService._validate_review_snapshot(connection, proposal, expected_snapshot)
            with sqlite3.connect(database, timeout=0) as competing:
                with pytest.raises(sqlite3.OperationalError, match="locked"):
                    competing.execute("UPDATE source_fragments SET text = 'changed'")

    service = ContendedRecords(database)
    proposal = getattr(service, f"propose_{kind}_record")(source.id, parts)
    snapshot = record_review_snapshot(proposal, parts)
    create = service.create_from_proposal if kind == "travel" else getattr(service, f"create_{kind}_from_proposal")
    record = create(proposal, expected_snapshot=snapshot)
    assert record.id is not None
    with sqlite3.connect(database) as connection:
        connection.execute("UPDATE source_fragments SET text = 'changed'")
    with pytest.raises(ValueError, match="stale"):
        create(proposal, expected_snapshot=snapshot)
    assert len(getattr(service, f"list_{kind}_records")()) == 1


def test_travel_record_keeps_its_source_reference(tmp_path: Path) -> None:
    database=tmp_path / "db.sqlite"; initialize_database(database); time=datetime(2026,9,8,tzinfo=UTC)
    source=SourceRepository(database).add(Source(None,tmp_path / "trip.pdf","a"*64,SourceType.PDF,0,time,time,time))
    record=RecordService(database).create_travel_record(TravelRecord(None,source.id or 0,"SQ638","Singapore","Tokyo",time,None,"ABC"))
    fragment=SourceFragmentRepository(database).replace_for_source(ExtractionResult(source.id or 0,(SourceFragment(None,source.id or 0,None,0,"SQ638 to Tokyo","page 1"),)))[0]
    service=RecordService(database); service.add_field_evidence(record.id or 0,"flight_number",fragment.id or 0)
    assert service.list_travel_records() == [record]

def test_travel_record_proposal_keeps_field_level_fragment_evidence(tmp_path: Path) -> None:
    service=RecordService(tmp_path / "unused.db")
    proposal=service.propose_travel_record(4,[(8,"Flight SQ638\nDeparture: Singapore\nArrival: Tokyo\nPNR: ABC\nDeparture Time: 2026-10-01T09:00:00+08:00")])
    assert proposal.record.flight_number == "SQ638" and proposal.field_evidence["arrival"] == 8
    assert proposal.record.departure_time is not None
    assert proposal.record.departure_time.isoformat() == "2026-10-01T09:00:00+08:00"


def test_travel_record_passenger_is_evidenced_persisted_and_correctable(tmp_path: Path) -> None:
    database = tmp_path / "steward.db"; initialize_database(database)
    now = datetime(2026, 9, 8, tzinfo=UTC)
    source = SourceRepository(database).add(
        Source(None, tmp_path / "trip.txt", "a" * 64, SourceType.PLAIN_TEXT, 0, now, now, now)
    )
    fragment = SourceFragmentRepository(database).replace_for_source(ExtractionResult(source.id or 0, (
        SourceFragment(None, source.id or 0, None, 0, "Flight SQ638\nPassenger Name: Ada Lovelace\nPNR: ABC", "entire file"),
    )))[0]
    service = RecordService(database)

    proposal = service.propose_travel_record(source.id or 0, [(fragment.id or 0, fragment.text)])
    record = service.create_from_proposal(proposal)

    assert record.passenger == "Ada Lovelace"
    assert service.field_evidence("travel", record.id or 0)["passenger"] == fragment.id
    corrected = service.correct_travel_field(record.id or 0, "passenger", "Grace Hopper")
    assert corrected.passenger == "Grace Hopper"
    assert service.get_travel_record(record.id or 0).passenger == "Grace Hopper"

def test_accepted_proposal_persists_its_evidence_atomically(tmp_path: Path) -> None:
    database = tmp_path / "db.sqlite"; initialize_database(database); time = datetime(2026, 9, 8, tzinfo=UTC)
    source = SourceRepository(database).add(Source(None, tmp_path / "trip.pdf", "a" * 64, SourceType.PDF, 0, time, time, time))
    fragment = SourceFragmentRepository(database).replace_for_source(
        ExtractionResult(source.id or 0, (SourceFragment(None, source.id or 0, None, 0, "Flight SQ638", "page 1"),))
    )[0]
    service = RecordService(database)
    proposal = service.propose_travel_record(source.id or 0, [(fragment.id or 0, fragment.text)])
    record = service.create_from_proposal(proposal)

    with sqlite3.connect(database) as connection:
        evidence = connection.execute("SELECT field_name, fragment_id FROM travel_record_evidence").fetchall()
    assert service.list_travel_records() == [record]
    assert evidence == [("flight_number", fragment.id)]


def test_receipt_record_proposal_persists_only_evidenced_fields(tmp_path: Path) -> None:
    database = tmp_path / "steward.db"; initialize_database(database); now = datetime(2026, 9, 9, tzinfo=UTC)
    source = SourceRepository(database).add(Source(None, tmp_path / "receipt.txt", "a" * 64, SourceType.PLAIN_TEXT, 0, now, now, now))
    fragment = SourceFragmentRepository(database).replace_for_source(ExtractionResult(source.id or 0, (SourceFragment(None, source.id or 0, None, 0, "Merchant: Corner Store\nTotal: SGD 12.50\nReceipt Number: R-42", "entire file"),)))[0]
    service = RecordService(database)
    proposal = service.propose_receipt_record(source.id or 0, [(fragment.id or 0, fragment.text)])
    assert proposal.record.merchant == "Corner Store"
    assert proposal.record.total_cents == 1250
    assert proposal.record.currency == "SGD"
    assert proposal.record.receipt_number == "R-42"
    assert proposal.field_evidence["total_cents"] == fragment.id
    receipt = service.create_receipt_from_proposal(proposal)
    with sqlite3.connect(database) as connection:
        evidence = connection.execute("SELECT field_name, fragment_id FROM receipt_record_evidence ORDER BY field_name").fetchall()
    assert receipt.id is not None
    assert ("total_cents", fragment.id) in evidence
    assert service.field_evidence("receipt", receipt.id)["total_cents"] == fragment.id
    assert service.list_receipt_records() == [receipt]


def test_warranty_record_proposal_persists_field_evidence(tmp_path: Path) -> None:
    database = tmp_path / "steward.db"; initialize_database(database); now = datetime(2026, 9, 9, tzinfo=UTC)
    source = SourceRepository(database).add(Source(None, tmp_path / "warranty.txt", "b" * 64, SourceType.PLAIN_TEXT, 0, now, now, now))
    fragment = SourceFragmentRepository(database).replace_for_source(ExtractionResult(source.id or 0, (SourceFragment(None, source.id or 0, None, 0, "Product: Laptop Pro\nProvider: Example Corp\nWarranty Number: W-100\nCoverage Ends: 2028-09-09T00:00:00+08:00", "entire file"),)))[0]
    service = RecordService(database)
    proposal = service.propose_warranty_record(source.id or 0, [(fragment.id or 0, fragment.text)])
    assert proposal.record.product_name == "Laptop Pro"
    assert proposal.record.warranty_number == "W-100"
    warranty = service.create_warranty_from_proposal(proposal)
    assert service.field_evidence("warranty", warranty.id or 0)["product_name"] == fragment.id
    assert service.list_warranty_records() == [warranty]


def test_receipt_and_warranty_corrections_validate_and_preserve_sources(tmp_path: Path) -> None:
    database = tmp_path / "steward.db"; initialize_database(database); now = datetime(2026, 9, 9, tzinfo=UTC)
    source = SourceRepository(database).add(Source(None, tmp_path / "records.txt", "c" * 64, SourceType.PLAIN_TEXT, 0, now, now, now))
    fragment = SourceFragmentRepository(database).replace_for_source(
        ExtractionResult(source.id or 0, (SourceFragment(None, source.id or 0, None, 0, "records", "entire file"),))
    )[0]
    service = RecordService(database)
    receipt = service.create_receipt_from_proposal(
        ReceiptRecordProposal(ReceiptRecord(None, source.id or 0, "Corner Store", 1250, "SGD", None, "R-42"), {"merchant": fragment.id or 0})
    )
    warranty = service.create_warranty_from_proposal(
        WarrantyRecordProposal(WarrantyRecord(None, source.id or 0, "Laptop", "Example", "W-1", None), {"product_name": fragment.id or 0})
    )

    corrected_receipt = service.correct_receipt_field(receipt.id or 0, "total_cents", "14.75")
    corrected_warranty = service.correct_warranty_field(warranty.id or 0, "provider", "Example Care")

    assert corrected_receipt.total_cents == 1475
    assert corrected_receipt.source_id == source.id
    assert corrected_warranty.provider == "Example Care"
    with pytest.raises(ValueError, match="three-letter"):
        service.validate_receipt_field("currency", "Singapore dollars")
    with pytest.raises(ValueError, match="Warranty field"):
        service.validate_warranty_field("merchant", "Example")

def test_empty_travel_proposal_cannot_create_an_empty_record(tmp_path: Path) -> None:
    database = tmp_path / "db.sqlite"; initialize_database(database)
    proposal = RecordService(database).propose_travel_record(1, [(2, "unstructured note")])

    with pytest.raises(ValueError, match="evidenced"):
        RecordService(database).create_from_proposal(proposal)


def test_travel_record_references_are_source_backed_and_idempotent(tmp_path: Path) -> None:
    database = tmp_path / "db.sqlite"; initialize_database(database)
    time = datetime(2026, 9, 8, tzinfo=UTC)
    source = SourceRepository(database).add(
        Source(None, tmp_path / "trip.pdf", "a" * 64, SourceType.PDF, 0, time, time, time)
    )
    fragment = SourceFragmentRepository(database).replace_for_source(
        ExtractionResult(source.id or 0, (
            SourceFragment(None, source.id or 0, None, 0, "Booking portal: https://example.com/ABC", "page 1"),
        ))
    )[0]
    service = RecordService(database)
    record = service.create_travel_record(
        TravelRecord(None, source.id or 0, "SQ638", "Singapore", "Tokyo", time, None, "ABC")
    )

    first = service.add_reference(record.id or 0, "Booking Portal", "https://example.com/ABC", fragment.id or 0)
    repeated = service.add_reference(record.id or 0, "booking_portal", " https://example.com/ABC ", fragment.id or 0)

    assert first.id == repeated.id
    assert service.list_references(record.id or 0) == (first,)


def test_travel_record_reference_rejects_unproven_or_malformed_values(tmp_path: Path) -> None:
    database = tmp_path / "db.sqlite"; initialize_database(database)
    service = RecordService(database)

    with pytest.raises(ValueError, match="Reference type"):
        service.add_reference(1, "Booking URL!", "https://example.com", 1)
    with pytest.raises(ValueError, match="Reference value"):
        service.add_reference(1, "booking_url", " ", 1)


def test_travel_reference_requires_evidence_from_the_record_source_and_text(tmp_path: Path) -> None:
    database = tmp_path / "db.sqlite"; initialize_database(database)
    time = datetime(2026, 9, 8, tzinfo=UTC)
    sources = SourceRepository(database)
    source = sources.add(Source(None, tmp_path / "trip.pdf", "a" * 64, SourceType.PDF, 0, time, time, time))
    other = sources.add(Source(None, tmp_path / "other.pdf", "b" * 64, SourceType.PDF, 0, time, time, time))
    fragments = SourceFragmentRepository(database)
    supported = fragments.replace_for_source(ExtractionResult(source.id or 0, (
        SourceFragment(None, source.id or 0, None, 0, "Booking: https://example.com/ABC", "page 1"),
    )))[0]
    unrelated = fragments.replace_for_source(ExtractionResult(other.id or 0, (
        SourceFragment(None, other.id or 0, None, 0, "Other: https://example.com/ABC", "page 1"),
    )))[0]
    service = RecordService(database)
    record = service.create_travel_record(TravelRecord(None, source.id or 0, "SQ638", None, None, None, None, None))

    with pytest.raises(ValueError, match="belong"):
        service.add_reference(record.id or 0, "booking_url", "https://example.com/ABC", unrelated.id or 0)
    with pytest.raises(ValueError, match="appear"):
        service.add_reference(record.id or 0, "booking_url", "https://example.com/NOPE", supported.id or 0)
