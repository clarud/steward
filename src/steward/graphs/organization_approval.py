"""Resumable human approval for an organization proposal."""
from __future__ import annotations
from typing import Callable, NotRequired, TypedDict
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt
from steward.organization import OrganizationProposalRepository

class OrganizationApprovalState(TypedDict):
    proposal_id: int
    decision: NotRequired[str]
    status: NotRequired[str]

def build_organization_approval_graph(
    repository: OrganizationProposalRepository,
    *,
    checkpointer: object,
    review_proposal: Callable[[int, str], object] | None = None,
):
    def request_approval(state: OrganizationApprovalState) -> dict[str, str]:
        decision = interrupt({"proposal_id": state["proposal_id"], "question": "Accept this organization proposal?"})
        if decision not in {"accepted", "rejected"}:
            raise ValueError("Approval decision must be accepted or rejected.")
        return {"decision": decision}

    def record_decision(state: OrganizationApprovalState) -> dict[str, str]:
        if review_proposal is None:
            repository.set_status(state["proposal_id"], state["decision"])
        else:
            review_proposal(state["proposal_id"], state["decision"])
        return {"status": state["decision"]}

    builder = StateGraph(OrganizationApprovalState)
    builder.add_node("request_approval", request_approval)
    builder.add_node("record_decision", record_decision)
    builder.add_edge(START, "request_approval")
    builder.add_edge("request_approval", "record_decision")
    builder.add_edge("record_decision", END)
    return builder.compile(checkpointer=checkpointer)
