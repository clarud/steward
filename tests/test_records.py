from datetime import UTC, datetime
from pathlib import Path
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
    proposal=service.propose_travel_record(4,[(8,"Flight SQ638\nDeparture: Singapore\nArrival: Tokyo\nPNR: ABC")])
    assert proposal.record.flight_number == "SQ638" and proposal.field_evidence["arrival"] == 8
