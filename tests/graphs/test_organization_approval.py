from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command
from steward.graphs import build_organization_approval_graph

class FakeRepository:
    def __init__(self): self.calls = []
    def set_status(self, proposal_id, status): self.calls.append((proposal_id, status))

def test_approval_graph_pauses_then_records_resumed_decision() -> None:
    repository = FakeRepository()
    graph = build_organization_approval_graph(repository, checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": "telegram:100"}}
    paused = graph.invoke({"proposal_id": 3}, config)
    assert "__interrupt__" in paused and repository.calls == []
    result = graph.invoke(Command(resume="accepted"), config)
    assert result["status"] == "accepted"
    assert repository.calls == [(3, "accepted")]
