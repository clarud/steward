from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import tool

from steward.graphs import GeminiToolCallingModel, build_tool_agent_graph
from steward.tools import ToolDefinition, ToolPolicy, ToolRisk


class ToolCallingFakeModel:
    def __init__(self) -> None:
        self.bound_tool_names: list[str] = []
        self.calls = 0

    def bind_tools(self, tools):
        self.bound_tool_names = [tool.name for tool in tools]
        return self

    def invoke(self, messages):
        self.calls += 1
        if any(isinstance(message, ToolMessage) for message in messages):
            return AIMessage("The source says: TLBs cache translations.")
        return AIMessage(
            "",
            tool_calls=[{"name": "search_sources", "args": {"query": "TLB"}, "id": "call-1"}],
        )


def test_tool_agent_runs_model_tool_model_loop() -> None:
    @tool
    def search_sources(query: str) -> str:
        """Search sources by query."""
        return f"result for {query}"

    model = ToolCallingFakeModel()
    graph = build_tool_agent_graph(model, [search_sources])

    result = graph.invoke({"messages": [HumanMessage("What is a TLB?")]})

    assert model.bound_tool_names == ["search_sources"]
    assert model.calls == 2
    assert isinstance(result["messages"][-2], ToolMessage)
    assert result["messages"][-2].content == "result for TLB"
    assert result["messages"][-1].content == "The source says: TLBs cache translations."


def test_tool_agent_requires_at_least_one_tool() -> None:
    try:
        build_tool_agent_graph(ToolCallingFakeModel(), [])
    except ValueError as error:
        assert "at least one tool" in str(error)
    else:
        raise AssertionError("An empty tool list must fail.")


def test_gemini_tool_adapter_converts_function_calls_to_ai_tool_calls() -> None:
    class FunctionCall:
        name = "search_sources"
        args = {"query": "TLB"}
        id = "gemini-call-1"

    class Response:
        text = ""
        function_calls = [FunctionCall()]

    class Models:
        def generate_content(self, **kwargs):
            self.kwargs = kwargs
            return Response()

    class Client:
        models = Models()

    @tool
    def search_sources(query: str) -> str:
        """Search sources by query."""
        return query

    adapter = GeminiToolCallingModel(api_key="test", model="gemini-test", client=Client()).bind_tools([search_sources])
    result = adapter.invoke([HumanMessage("Find TLB notes")])

    assert result.tool_calls == [{"name": "search_sources", "args": {"query": "TLB"}, "id": "gemini-call-1", "type": "tool_call"}]


def test_gemini_tool_adapter_replays_langgraph_tool_messages_with_sdk_supported_fields() -> None:
    from google.genai import types

    contents = GeminiToolCallingModel._contents(
        [
            AIMessage(
                "",
                tool_calls=[{"name": "search_sources", "args": {"query": "TLB"}, "id": "call-1"}],
                additional_kwargs={"gemini_thought_signatures": {"call-1": b"opaque"}},
            ),
            ToolMessage("[]", name="search_sources", tool_call_id="call-1"),
        ],
        types,
    )

    assert contents[0].parts[0].function_call.name == "search_sources"
    assert dict(contents[0].parts[0].function_call.args) == {"query": "TLB"}
    assert contents[0].parts[0].function_call.id == "call-1"
    assert contents[0].parts[0].thought_signature == b"opaque"
    assert contents[1].parts[0].function_response.name == "search_sources"
    assert dict(contents[1].parts[0].function_response.response) == {"result": "[]"}
    assert contents[1].parts[0].function_response.id == "call-1"


def test_gemini_tool_adapter_preserves_thought_signature_from_response_part() -> None:
    class FunctionCall:
        name = "search_records"
        args = {"query": "calendar"}
        id = "thought-call"

    class Part:
        function_call = FunctionCall()
        thought_signature = b"opaque-signature"

    class Response:
        text = ""
        function_calls = []
        candidates = [type("Candidate", (), {"content": type("Content", (), {"parts": [Part()]})()})()]

    class Models:
        def generate_content(self, **_kwargs):
            return Response()

    class Client:
        models = Models()

    result = GeminiToolCallingModel(api_key="test", model="gemini-test", client=Client()).invoke([HumanMessage("Find my calendar")])

    assert result.additional_kwargs["gemini_thought_signatures"] == {"thought-call": b"opaque-signature"}


def test_tool_policy_blocks_an_unapproved_write_inside_tool_node() -> None:
    calls = []

    @tool
    def create_calendar_event(title: str) -> str:
        """Create an event."""
        calls.append(title)
        return "created"

    class WriteRequestingModel(ToolCallingFakeModel):
        def invoke(self, messages):
            self.calls += 1
            if any(isinstance(message, ToolMessage) for message in messages):
                return AIMessage("I need your approval before creating that event.")
            return AIMessage("", tool_calls=[{"name": "create_calendar_event", "args": {"title": "Flight"}, "id": "call-write"}])

    policy = ToolPolicy([
        ToolDefinition("create_calendar_event", True, ToolRisk.SENSITIVE_WRITE, "idempotency key", "google_calendar", True)
    ])
    graph = build_tool_agent_graph(WriteRequestingModel(), [create_calendar_event], tool_policy=policy)

    result = graph.invoke({"messages": [HumanMessage("Add my flight") ]})

    assert calls == []
    assert "requires explicit user approval" in result["messages"][-2].content
