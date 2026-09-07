from datetime import UTC, datetime
from pathlib import Path
from steward.actions import FileMutationService
from steward.activity import ActivityService
from steward.organization import OrganizationService
from steward.sources import Source, SourceRepository, SourceType
from steward.storage import initialize_database
from steward.workspaces import Workspace

def test_accepted_matching_proposal_can_move_registered_source(tmp_path: Path) -> None:
    database=tmp_path / "db.sqlite"; initialize_database(database); original=tmp_path / "inbox" / "steward.md"; original.parent.mkdir(); original.write_text("x")
    time=datetime(2026,9,8,tzinfo=UTC); repository=SourceRepository(database)
    source=repository.add(Source(None,original.resolve(),"a"*64,SourceType.MARKDOWN,1,time,time,time))
    proposal=OrganizationService().propose(source,[Workspace(1,"Steward","active",time)])
    FileMutationService(repository,ActivityService(database)).move_source(source.path, tmp_path / proposal.suggested_path)
    assert repository.get_by_id(source.id or 0).path.is_file()
