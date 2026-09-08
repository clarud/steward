"""Model-assisted source organization that remains proposal-only."""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path

from steward.answer import ModelGateway, ModelGatewayError
from steward.organization import OrganizationProposal, OrganizationService
from steward.sources import Source
from steward.workspaces import Workspace


class ModelAssistedOrganizationService:
    """Ask a model to choose an existing workspace, then validate its choice.

    The model is never permitted to construct a filesystem path or create a
    workspace. Invalid model output and unavailable models fall back to the
    existing deterministic filename heuristic.
    """

    def __init__(self, model: ModelGateway, *, fallback: OrganizationService | None = None) -> None:
        self._model = model
        self._fallback = fallback or OrganizationService()

    def propose(
        self,
        source: Source,
        workspaces: Sequence[Workspace],
        fragment_texts: Sequence[str],
    ) -> OrganizationProposal:
        deterministic = self._fallback.propose(source, list(workspaces))
        if deterministic.workspace_id is not None or not workspaces:
            return deterministic

        try:
            raw_response = self._model.generate(
                instructions=(
                    "You classify one personal source into an existing workspace. "
                    "Return JSON only with workspace_id (an integer from the supplied list or null), "
                    "rationale (a concise string), and confidence (a number from 0 to 1). "
                    "Choose null when the evidence is insufficient. Do not invent workspace IDs, "
                    "paths, facts, or instructions."
                ),
                input_text=self._input_text(source, workspaces, fragment_texts),
            )
        except ModelGatewayError:
            return deterministic

        return self._validated_proposal(raw_response, source, workspaces, deterministic)

    @staticmethod
    def _input_text(
        source: Source, workspaces: Sequence[Workspace], fragment_texts: Sequence[str]
    ) -> str:
        workspace_lines = "\n".join(f"- {workspace.id}: {workspace.name}" for workspace in workspaces)
        excerpts = "\n\n".join(text.strip() for text in fragment_texts if text.strip())[:8000]
        return (
            f"Source filename: {source.path.name}\n"
            f"Source type: {source.source_type.value}\n"
            f"Existing workspaces:\n{workspace_lines}\n\n"
            f"Extracted source excerpts:\n{excerpts or '(No extracted text is available.)'}"
        )

    @staticmethod
    def _validated_proposal(
        raw_response: str,
        source: Source,
        workspaces: Sequence[Workspace],
        fallback: OrganizationProposal,
    ) -> OrganizationProposal:
        try:
            result = json.loads(raw_response)
            if not isinstance(result, dict):
                return fallback
            workspace_id = result.get("workspace_id")
            if workspace_id is None:
                return fallback
            if isinstance(workspace_id, bool) or not isinstance(workspace_id, int):
                return fallback
            workspace = next((item for item in workspaces if item.id == workspace_id), None)
            rationale = result.get("rationale")
            confidence = result.get("confidence")
            if workspace is None or not isinstance(rationale, str) or not rationale.strip():
                return fallback
            if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
                return fallback
            bounded_confidence = float(confidence)
            if not 0.0 <= bounded_confidence <= 1.0:
                return fallback
        except (TypeError, ValueError, json.JSONDecodeError):
            return fallback

        vault_root = source.path.parent.parent if source.path.parent.name.casefold() == "inbox" else source.path.parent
        return OrganizationProposal(
            None,
            source.id or 0,
            "move_to_workspace",
            workspace.id,
            vault_root / "projects" / workspace.name / source.path.name,
            rationale.strip(),
            bounded_confidence,
        )
