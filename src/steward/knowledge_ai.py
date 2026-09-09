"""Evidence-bounded model assistance for non-mutating knowledge proposals."""

from __future__ import annotations

import json

from steward.answer import ModelGateway, ModelGatewayError
from steward.extraction import SourceFragment
from steward.knowledge import Claim, EnrichmentOperation, KnowledgeEnrichmentProposal, KnowledgeService


class ModelAssistedKnowledgeService:
    """Ask for a classification only; deterministic code validates every field."""

    def __init__(self, model: ModelGateway, *, fallback: KnowledgeService) -> None:
        self._model = model
        self._fallback = fallback

    def compare_evidence(self, claim: Claim, fragment: SourceFragment) -> KnowledgeEnrichmentProposal:
        fallback = self._fallback.compare_evidence(
            claim, fragment_id=fragment.id or 0, evidence_text=fragment.text
        )
        try:
            raw = self._model.generate(
                instructions=(
                    "Compare one existing claim with one supplied evidence fragment. Return JSON only: "
                    "operation must be exactly confirm, extend, refine, qualify, or contradict; rationale "
                    "must be a concise explanation grounded only in the supplied text. Do not invent facts."
                ),
                input_text=(
                    f"Claim:\n{claim.text}\n\nEvidence fragment ({fragment.location}):\n{fragment.text}"
                ),
            )
            result = json.loads(raw)
            operation = EnrichmentOperation(result["operation"])
            rationale = result["rationale"]
            if not isinstance(rationale, str) or not rationale.strip() or len(rationale) > 500:
                return fallback
        except (KeyError, TypeError, ValueError, json.JSONDecodeError, ModelGatewayError):
            return fallback
        return KnowledgeEnrichmentProposal(claim.id or 0, fragment.id or 0, operation, rationale.strip())
