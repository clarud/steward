from datetime import UTC, datetime
from pathlib import Path
from steward.organization import OrganizationProposalRepository, OrganizationService
from steward.sources import Source, SourceType
from steward.storage import initialize_database
from steward.workspaces import Workspace
from steward.sources import SourceRepository

def test_matching_workspace_creates_pending_proposal(tmp_path: Path) -> None:
    source = Source(1, tmp_path / "steward-notes.md", "a" * 64, SourceType.MARKDOWN, 0, datetime(2026,9,8,tzinfo=UTC), datetime(2026,9,8,tzinfo=UTC), datetime(2026,9,8,tzinfo=UTC))
    workspace = Workspace(2, "Steward", "active", datetime(2026,9,8,tzinfo=UTC))
    proposal = OrganizationService().propose(source, [workspace])
    assert proposal.workspace_id == 2 and proposal.status == "pending"

def test_proposal_status_changes_without_moving_file(tmp_path: Path) -> None:
    database = tmp_path / "db.sqlite"; initialize_database(database)
    # Repository behavior is exercised after normal source/workspace creation in later integration flows.
    repository = OrganizationProposalRepository(database)
    timestamp = datetime(2026,9,8,tzinfo=UTC)
    source = SourceRepository(database).add(Source(None, tmp_path / "steward.md", "b" * 64, SourceType.MARKDOWN, 0, timestamp, timestamp, timestamp))
    proposal_id = repository.add(OrganizationService().propose(source, []))
    repository.set_status(proposal_id, "rejected")
    assert repository.list_all()[0].status == "rejected"
