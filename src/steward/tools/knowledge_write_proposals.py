"""Reviewable knowledge-enrichment proposals exposed to the agent tool loop."""

from __future__ import annotations

import json

from langchain_core.tools import BaseTool, tool

from steward.activity import ActivityService, ActivityType
from steward.extraction import SourceFragmentRepository
from steward.knowledge import KnowledgeEnrichmentProposalRepository, KnowledgeService
from steward.tools.policy import ToolDefinition, ToolRisk


KNOWLEDGE_PROPOSAL_TOOL_DEFINITIONS = [
    ToolDefinition(
        "propose_knowledge_enrichment",
        True,
        ToolRisk.SAFE_WRITE,
        "same claim, fragment, operation, and rationale reuse one pending proposal",
        None,
        False,
    )
]


class KnowledgeProposalToolService:
    """Persist a comparison proposal; never alter canonical claim text or evidence."""

    def __init__(
        self,
        knowledge: KnowledgeService,
        fragments: SourceFragmentRepository,
        proposals: KnowledgeEnrichmentProposalRepository,
        activity: ActivityService,
    ) -> None:
        self._knowledge = knowledge
        self._fragments = fragments
        self._proposals = proposals
        self._activity = activity

    def propose_knowledge_enrichment(self, claim_id: int, fragment_id: int) -> str:
        """Create one pending evidence comparison after deterministic validation."""

        claim = self._knowledge.get_claim(claim_id)
        fragment = self._fragments.get(fragment_id)
        if claim is None:
            return json.dumps({"error": f"Claim {claim_id} was not found."})
        if fragment is None:
            return json.dumps({"error": f"Fragment {fragment_id} was not found."})
        derived = self._knowledge.compare_evidence(
            claim, fragment_id=fragment.id or 0, evidence_text=fragment.text
        )
        stored = self._proposals.add(derived)
        if stored.status == "pending":
            self._activity.record(
                ActivityType.KNOWLEDGE_ENRICHMENT_PROPOSED,
                object_id=str(stored.id),
                details=f"{stored.operation.value}: {stored.rationale}",
            )
        return json.dumps(
            {
                "status": stored.status,
                "proposal_id": stored.id,
                "operation": stored.operation.value,
                "claim_id": stored.claim_id,
                "fragment_id": stored.fragment_id,
                "rationale": stored.rationale,
                "review_command": f"steward review-knowledge-enrichment {stored.id} accepted",
            }
        )


def build_knowledge_proposal_tools(service: KnowledgeProposalToolService) -> list[BaseTool]:
    """Return the one proposal-only knowledge tool for the LangGraph agent."""

    @tool
    def propose_knowledge_enrichment(claim_id: int, fragment_id: int) -> str:
        """Create a pending claim/evidence comparison for later human review. Never changes a claim now."""

        return service.propose_knowledge_enrichment(claim_id, fragment_id)

    return [propose_knowledge_enrichment]
