from datetime import UTC, datetime
from pathlib import Path

from steward.answer import ModelGatewayError
from steward.organization_ai import ModelAssistedOrganizationService
from steward.sources import Source, SourceType
from steward.workspaces import Workspace


class FakeModel:
    def __init__(self, response: str | Exception) -> None:
        self.response = response
        self.input_text = ""

    def generate(self, *, instructions: str, input_text: str) -> str:
        self.input_text = input_text
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


def _source(tmp_path: Path) -> Source:
    now = datetime(2026, 9, 9, tzinfo=UTC)
    return Source(
        3, tmp_path / "vault" / "inbox" / "lecture.md", "a" * 64,
        SourceType.MARKDOWN, 10, now, now, now,
    )


def _workspace() -> Workspace:
    return Workspace(7, "Operating Systems", "active", datetime(2026, 9, 9, tzinfo=UTC))


def test_model_assisted_organization_validates_an_existing_workspace_choice(tmp_path: Path) -> None:
    model = FakeModel(
        '{"workspace_id": 7, "rationale": "The excerpt discusses page tables.", "confidence": 0.8}'
    )

    proposal = ModelAssistedOrganizationService(model).propose(
        _source(tmp_path), [_workspace()], ["# TLB\nPage translation cache."],
    )

    assert proposal.workspace_id == 7
    assert proposal.suggested_path == tmp_path / "vault" / "projects" / "Operating Systems" / "lecture.md"
    assert proposal.confidence == 0.8
    assert "Page translation" in model.input_text


def test_invalid_or_unavailable_model_output_keeps_a_source_in_inbox(tmp_path: Path) -> None:
    for response in (
        '{"workspace_id": 99, "rationale": "invented", "confidence": 0.9}',
        ModelGatewayError("offline"),
    ):
        proposal = ModelAssistedOrganizationService(FakeModel(response)).propose(
            _source(tmp_path), [_workspace()], ["unrelated text"],
        )
        assert proposal.proposal_type == "keep_in_inbox"
        assert proposal.suggested_path is None
