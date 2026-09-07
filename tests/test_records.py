from datetime import UTC, datetime
from pathlib import Path
import sqlite3
import pytest
from steward.records import RecordService, TravelRecord
from steward.sources import Source, SourceRepository, SourceType
from steward.storage import initialize_database
from steward.extraction import ExtractionResult, SourceFragment, SourceFragmentRepository

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

def test_empty_travel_proposal_cannot_create_an_empty_record(tmp_path: Path) -> None:
    database = tmp_path / "db.sqlite"; initialize_database(database)
    proposal = RecordService(database).propose_travel_record(1, [(2, "unstructured note")])

    with pytest.raises(ValueError, match="evidenced"):
        RecordService(database).create_from_proposal(proposal)
