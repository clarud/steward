from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import tool
from langgraph.checkpoint.sqlite import SqliteSaver
import sqlite3

from steward.graphs import (
    GeminiToolCallingModel,
    OllamaToolCallingModel,
    OpenAICompatibleToolCallingModel,
    build_tool_agent_graph,
)
from steward.answer.gateway import ModelGatewayError
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


def test_tool_agent_ends_cleanly_when_model_exceeds_tool_budget() -> None:
    @tool
    def search_sources(query: str) -> str:
        """Search sources."""
        return query

    class LoopingModel:
        def bind_tools(self, _tools):
            return self

        def invoke(self, _messages):
            return AIMessage(
                "",
                tool_calls=[{"name": "search_sources", "args": {"query": "again"}, "id": "loop"}],
            )

    result = build_tool_agent_graph(LoopingModel(), [search_sources], max_tool_calls=2).invoke(
        {"messages": [HumanMessage("Search forever")]}
    )

    assert "tool-call limit" in result["messages"][-1].content


def test_tool_agent_does_not_execute_an_oversized_single_tool_call_batch() -> None:
    calls = []

    @tool
    def search_sources(query: str) -> str:
        """Search sources."""
        calls.append(query)
        return query

    class BulkToolModel:
        def bind_tools(self, _tools):
            return self

        def invoke(self, _messages):
            return AIMessage(
                "",
                tool_calls=[
                    {"name": "search_sources", "args": {"query": str(index)}, "id": f"bulk-{index}"}
                    for index in range(5)
                ],
            )

    result = build_tool_agent_graph(BulkToolModel(), [search_sources], max_tool_calls=4).invoke(
        {"messages": [HumanMessage("Search everything")]}
    )

    assert calls == []
    assert "tool-call limit" in result["messages"][-1].content


def test_tool_agent_rejects_non_positive_tool_budget() -> None:
    @tool
    def search_sources(query: str) -> str:
        """Search sources."""
        return query

    try:
        build_tool_agent_graph(ToolCallingFakeModel(), [search_sources], max_tool_calls=0)
    except ValueError as error:
        assert "max_tool_calls" in str(error)
    else:
        raise AssertionError("A non-positive tool budget must fail.")


def test_tool_agent_returns_a_final_message_when_provider_is_unavailable() -> None:
    @tool
    def search_sources(query: str) -> str:
        """Search sources."""
        return query

    class UnavailableModel:
        def bind_tools(self, _tools):
            return self

        def invoke(self, _messages):
            raise ModelGatewayError("quota")

    result = build_tool_agent_graph(UnavailableModel(), [search_sources]).invoke(
        {"messages": [HumanMessage("Find notes")]}
    )

    assert "temporarily unavailable" in result["messages"][-1].content


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


def test_ollama_tool_adapter_sends_schemas_and_converts_tool_calls() -> None:
    captured = {}

    class Response:
        def read(self):
            return b'{"message": {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": "search_sources", "arguments": {"query": "TLB"}}}]}}'

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    def opener(http_request, *, timeout):
        captured["url"] = http_request.full_url
        captured["payload"] = http_request.data
        captured["timeout"] = timeout
        return Response()

    @tool
    def search_sources(query: str) -> str:
        """Search sources by query."""
        return query

    adapter = OllamaToolCallingModel(model="qwen3", opener=opener).bind_tools([search_sources])
    result = adapter.invoke([HumanMessage("Find TLB notes")])

    payload = __import__("json").loads(captured["payload"])
    assert captured["url"] == "http://127.0.0.1:11434/api/chat"
    assert captured["timeout"] == 180
    assert payload["tools"][0]["function"]["name"] == "search_sources"
    assert payload["messages"] == [{"role": "user", "content": "Find TLB notes"}]
    assert result.tool_calls[0]["name"] == "search_sources"
    assert result.tool_calls[0]["args"] == {"query": "TLB"}
    assert result.tool_calls[0]["id"].startswith("ollama-")


def test_openai_compatible_tool_adapter_sends_client_executed_tools() -> None:
    recorded: dict[str, object] = {}

    class Function:
        name = "search_sources"
        arguments = '{"query": "TLB"}'

    class ToolCall:
        id = "call-1"
        function = Function()

    class Completion:
        choices = [type("Choice", (), {"message": type("Message", (), {"content": "", "tool_calls": [ToolCall()]})()})()]

    class Completions:
        @staticmethod
        def create(**kwargs):
            recorded.update(kwargs)
            return Completion()

    class Client:
        chat = type("Chat", (), {"completions": Completions()})()

    @tool
    def search_sources(query: str) -> str:
        """Search sources by query."""
        return query

    adapter = OpenAICompatibleToolCallingModel(
        api_key="test", model="llama3.1:8b", base_url="https://gateway.example/v1", client=Client()
    ).bind_tools([search_sources])
    result = adapter.invoke([HumanMessage("Find TLB notes")])

    assert recorded["model"] == "llama3.1:8b"
    assert recorded["messages"] == [{"role": "user", "content": "Find TLB notes"}]
    assert recorded["tools"][0]["function"]["name"] == "search_sources"
    assert result.tool_calls == [{"name": "search_sources", "args": {"query": "TLB"}, "id": "call-1", "type": "tool_call"}]


def test_openai_compatible_tool_adapter_replays_tool_messages() -> None:
    messages = OpenAICompatibleToolCallingModel._messages(
        [
            AIMessage("", tool_calls=[{"name": "search_sources", "args": {"query": "TLB"}, "id": "call-1"}]),
            ToolMessage("result", name="search_sources", tool_call_id="call-1"),
        ]
    )

    assert messages == [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"id": "call-1", "type": "function", "function": {"name": "search_sources", "arguments": '{"query": "TLB"}'}},
            ],
        },
        {"role": "tool", "tool_call_id": "call-1", "content": "result"},
    ]


def test_ollama_tool_adapter_replays_tool_calls_and_results() -> None:
    messages = OllamaToolCallingModel._messages(
        [
            AIMessage("", tool_calls=[{"name": "search_sources", "args": {"query": "TLB"}, "id": "call-1"}]),
            ToolMessage("result", name="search_sources", tool_call_id="call-1"),
        ]
    )

    assert messages == [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "type": "function",
                    "function": {"index": 0, "name": "search_sources", "arguments": {"query": "TLB"}},
                }
            ],
        },
        {"role": "tool", "tool_name": "search_sources", "content": "result"},
    ]


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


def test_gemini_tool_adapter_reads_candidate_text_and_handles_empty_completion() -> None:
    class Part:
        text = "Answer from a candidate part."

    class Response:
        text = ""
        function_calls = []
        candidates = [type("Candidate", (), {"content": type("Content", (), {"parts": [Part()]})()})()]

    class Models:
        def generate_content(self, **_kwargs):
            return Response()

    class Client:
        models = Models()

    adapter = GeminiToolCallingModel(api_key="test", model="gemini-test", client=Client())
    assert adapter.invoke([HumanMessage("Question")]).content == "Answer from a candidate part."

    Response.candidates = []
    assert "did not return a final answer" in adapter.invoke([HumanMessage("Question")]).content


def test_tool_graph_checkpointer_serializes_thought_signature_bytes() -> None:
    @tool
    def search_sources(query: str) -> str:
        """Search sources."""
        return query

    class SignatureModel:
        def bind_tools(self, _tools):
            return self

        def invoke(self, messages):
            if any(isinstance(message, ToolMessage) for message in messages):
                return AIMessage("Done")
            return AIMessage(
                "",
                tool_calls=[{"name": "search_sources", "args": {"query": "TLB"}, "id": "call-1"}],
                additional_kwargs={"gemini_thought_signatures": {"call-1": b"opaque"}},
            )

    connection = sqlite3.connect(":memory:", check_same_thread=False)
    checkpointer = SqliteSaver(connection); checkpointer.setup()
    result = build_tool_agent_graph(SignatureModel(), [search_sources], checkpointer=checkpointer).invoke(
        {"messages": [HumanMessage("Find TLB")]},
        {"configurable": {"thread_id": "signature-test"}},
    )

    assert result["messages"][-1].content == "Done"


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
