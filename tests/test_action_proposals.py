from pathlib import Path

import pytest

from steward.action_proposals import ActionProposalRepository
from steward.storage import initialize_database


def _repository(tmp_path: Path) -> ActionProposalRepository:
    database = tmp_path / "steward.db"
    initialize_database(database)
    return ActionProposalRepository(database)


def test_pending_proposal_is_found_by_its_exact_payload(tmp_path: Path) -> None:
    proposals = _repository(tmp_path)
    created = proposals.add("set_source_privacy", {"source_id": "1", "rule": "local_only"})

    assert proposals.find_pending("set_source_privacy", {"rule": "local_only", "source_id": "1"}) == created
    assert proposals.find_pending("set_source_privacy", {"source_id": "1", "rule": "external_allowed"}) is None


def test_reviewed_proposal_cannot_be_reviewed_again(tmp_path: Path) -> None:
    proposals = _repository(tmp_path)
    created = proposals.add("set_source_privacy", {"source_id": "1", "rule": "local_only"})

    proposals.set_status(created.id or 0, "accepted")

    reviewed = proposals.get(created.id or 0)
    assert reviewed is not None and reviewed.status == "accepted" and reviewed.reviewed_at is not None
    assert proposals.find_pending("set_source_privacy", created.payload) is None
    with pytest.raises(ValueError, match="already reviewed"):
        proposals.set_status(created.id or 0, "rejected")


def test_proposal_status_must_be_a_review_decision(tmp_path: Path) -> None:
    proposals = _repository(tmp_path)
    created = proposals.add("set_source_privacy", {"source_id": "1", "rule": "local_only"})

    with pytest.raises(ValueError, match="accepted or rejected"):
        proposals.set_status(created.id or 0, "pending")
