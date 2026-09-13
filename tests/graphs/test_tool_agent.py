from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import tool
from langgraph.checkpoint.sqlite import SqliteSaver
import sqlite3
import pytest

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


@pytest.mark.parametrize("invalid_arguments", [False, True])
def test_tool_graph_returns_secret_free_errors_to_model(invalid_arguments: bool) -> None:
    diagnostic = "token=synthetic-secret C:/private/state.db"
    calls = []

    @tool
    def inspect_item(identifier: int) -> str:
        """Inspect a registered item."""
        calls.append(identifier)
        raise RuntimeError(diagnostic)

    class Model:
        def bind_tools(self, tools):
            return self

        def invoke(self, messages):
            if isinstance(messages[-1], ToolMessage):
                failure = messages[-1]
                assert failure.status == "error"
                assert "synthetic-secret" not in failure.content
                assert "C:/private" not in failure.content
                assert "No reliable result" in failure.content
                return AIMessage("I could not verify that item.")
            return AIMessage("", tool_calls=[{"name": "inspect_item", "id": "failure-1",
                "args": {"identifier": diagnostic if invalid_arguments else 1}}])

    result = build_tool_agent_graph(Model(), [inspect_item]).invoke({"messages": [HumanMessage("Inspect my item")]})
    assert result["messages"][-1].content == "I could not verify that item."
    assert calls == ([] if invalid_arguments else [1])


def test_tool_error_boundary_preserves_langgraph_interrupts() -> None:
    from langgraph.types import Command, interrupt
    from langgraph.checkpoint.memory import InMemorySaver
    completed = []

    @tool
    def search_sources(query: str) -> str:
        """Synthetic tool that pauses for a decision."""
        decision = interrupt("Review before continuing")
        completed.append((query, decision))
        return f"Reviewed result: {decision}"

    graph = build_tool_agent_graph(ToolCallingFakeModel(), [search_sources], checkpointer=InMemorySaver())
    result = graph.invoke({"messages": [HumanMessage("Inspect") ]},
                          {"configurable": {"thread_id": "interrupt-test"}})
    assert "__interrupt__" in result
    assert not any(isinstance(message, ToolMessage) for message in result["messages"])
    assert completed == []
    resumed = graph.invoke(Command(resume="accepted"),
                           {"configurable": {"thread_id": "interrupt-test"}})
    replies = [message for message in resumed["messages"] if isinstance(message, ToolMessage)]
    assert len(replies) == 1
    assert replies[0].status == "success"
    assert replies[0].content == "Reviewed result: accepted"
    assert replies[0].tool_call_id == "call-1"
    assert completed == [("TLB", "accepted")]
    assert isinstance(resumed["messages"][-1], AIMessage)


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


def test_tool_agent_can_answer_from_the_last_allowed_tool_result() -> None:
    calls = []

    @tool
    def search_sources(query: str) -> str:
        """Search sources."""
        calls.append(query)
        return "Found OpenMP notes."

    class Model:
        def bind_tools(self, _tools):
            return self

        def invoke(self, messages):
            if isinstance(messages[-1], ToolMessage) or "tool budget is closed" in str(messages[-1].content):
                return AIMessage("I found your OpenMP notes.")
            return AIMessage("", tool_calls=[{"name": "search_sources", "args": {"query": "OpenMP"}, "id": "once"}])

    result = build_tool_agent_graph(Model(), [search_sources], max_tool_calls=1).invoke(
        {"messages": [HumanMessage("Find OpenMP notes")]}
    )
    assert calls == ["OpenMP"]
    assert result["messages"][-1].content == "I found your OpenMP notes."


def test_tool_agent_ends_cleanly_when_model_exceeds_tool_budget() -> None:
    @tool
    def search_sources(query: str) -> str:
        """Search sources."""
        return query

    class LoopingModel:
        def __init__(self):
            self.calls = 0

        def bind_tools(self, _tools):
            return self

        def invoke(self, _messages):
            self.calls += 1
            return AIMessage(
                "",
                tool_calls=[{"name": "search_sources", "args": {"query": f"again {self.calls}"}, "id": "loop"}],
            )

    result = build_tool_agent_graph(LoopingModel(), [search_sources], max_tool_calls=2).invoke(
        {"messages": [HumanMessage("Search forever")]}
    )

    assert "tool-call limit" in result["messages"][-1].content


def test_tool_agent_stops_before_repeating_an_identical_lookup() -> None:
    calls = []

    @tool
    def search_sources(query: str) -> str:
        """Search sources."""
        calls.append(query)
        return "[]"

    class RepeatingModel:
        def bind_tools(self, _tools):
            return self

        def invoke(self, _messages):
            return AIMessage(
                "",
                tool_calls=[{"name": "search_sources", "args": {"query": "missing notes"}, "id": "repeat"}],
            )

    result = build_tool_agent_graph(RepeatingModel(), [search_sources], max_tool_calls=4).invoke(
        {"messages": [HumanMessage("Find missing notes")]}
    )

    assert calls == ["missing notes"]
    assert "already completed that same local lookup" in result["messages"][-1].content


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


def test_gemini_tool_adapter_wraps_message_conversion_failures_as_provider_failures(monkeypatch) -> None:
    class Models:
        def generate_content(self, **_kwargs):
            raise AssertionError("message conversion should stop before the network call")

    class Client:
        models = Models()

    @tool
    def search_sources(query: str) -> str:
        """Search sources by query."""
        return query

    def invalid_contents(_messages, _types):
        raise TypeError("SDK message part is incompatible")

    monkeypatch.setattr(GeminiToolCallingModel, "_contents", staticmethod(invalid_contents))
    adapter = GeminiToolCallingModel(api_key="test", model="gemini-test", client=Client()).bind_tools([search_sources])

    try:
        adapter.invoke([HumanMessage("Find TLB notes")])
    except ModelGatewayError as error:
        assert "could not be completed" in str(error)
    else:
        raise AssertionError("SDK message conversion must become a recoverable provider failure.")


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


def test_openai_compatible_tool_adapter_uses_responses_client_executed_tools() -> None:
    recorded: dict[str, object] = {}

    class FunctionCall:
        type = "function_call"
        name = "search_sources"
        arguments = '{"query": "TLB"}'
        call_id = "call-1"

    class Response:
        output = [FunctionCall()]
        output_text = ""

    class Responses:
        @staticmethod
        def create(**kwargs):
            recorded.update(kwargs)
            return Response()

    class Client:
        responses = Responses()

    @tool
    def search_sources(query: str) -> str:
        """Search sources by query."""
        return query

    adapter = OpenAICompatibleToolCallingModel(
        api_key="test", model="llama3.1:8b", base_url="https://gateway.example/v1", client=Client()
    ).bind_tools([search_sources])
    result = adapter.invoke([SystemMessage("Use the supplied tools."), HumanMessage("Find TLB notes")])

    assert recorded["model"] == "llama3.1:8b"
    assert recorded["input"] == [{"role": "user", "content": "Find TLB notes"}]
    assert recorded["store"] is False
    assert recorded["instructions"] == "Use the supplied tools."
    assert recorded["tools"][0]["name"] == "search_sources"
    assert result.tool_calls == [{"name": "search_sources", "args": {"query": "TLB"}, "id": "call-1", "type": "tool_call"}]


def test_openai_compatible_tool_adapter_replays_tool_messages_as_response_items() -> None:
    messages = OpenAICompatibleToolCallingModel._input(
        [
            AIMessage("", tool_calls=[{"name": "search_sources", "args": {"query": "TLB"}, "id": "call-1"}]),
            ToolMessage("result", name="search_sources", tool_call_id="call-1"),
        ]
    )

    assert messages == [
        {"type": "function_call", "call_id": "call-1", "name": "search_sources", "arguments": '{"query": "TLB"}'},
        {"type": "function_call_output", "call_id": "call-1", "output": "result"},
    ]


def test_soclaas_transport_failure_is_a_secret_free_gateway_error(caplog) -> None:
    class Responses:
        @staticmethod
        def create(**_kwargs):
            raise RuntimeError("Bearer synthetic-secret at C:/private/soclaas.json")

    class Client:
        responses = Responses()

    adapter = OpenAICompatibleToolCallingModel(
        api_key="test", model="model", base_url="https://gateway.example/v1", client=Client()
    )

    with pytest.raises(ModelGatewayError) as failure:
        adapter.invoke([HumanMessage("Find notes")])

    assert str(failure.value) == "The OpenAI-compatible tool-agent request could not be completed."
    assert "synthetic-secret" not in str(failure.value) and "C:/private" not in str(failure.value)
    assert "synthetic-secret" not in caplog.text and "C:/private" not in caplog.text


@pytest.mark.parametrize("payload", [b"not-json", b"[]", b'{"response": "wrong shape"}'])
def test_ollama_transport_and_malformed_responses_are_bounded(payload: bytes) -> None:
    class Response:
        def read(self):
            return payload

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    adapter = OllamaToolCallingModel(model="qwen3", opener=lambda *_args, **_kwargs: Response())

    with pytest.raises(ModelGatewayError) as failure:
        adapter.invoke([HumanMessage("Find notes")])

    assert "Ollama" in str(failure.value)
    assert payload.decode("utf-8", errors="ignore") not in str(failure.value)


def test_ollama_transport_diagnostic_is_not_returned_to_the_caller() -> None:
    def unavailable(*_args, **_kwargs):
        raise OSError("C:/private/ollama.sock token=synthetic-secret")

    with pytest.raises(ModelGatewayError) as failure:
        OllamaToolCallingModel(model="qwen3", opener=unavailable).invoke([HumanMessage("Find notes")])

    assert str(failure.value) == "The local Ollama tool-agent request could not be completed."
    assert "synthetic-secret" not in str(failure.value) and "C:/private" not in str(failure.value)


def test_gemini_provider_diagnostic_is_not_returned_to_the_caller() -> None:
    class Models:
        @staticmethod
        def generate_content(**_kwargs):
            raise RuntimeError("C:/private/gemini-token.json contains synthetic-secret")

    class Client:
        models = Models()

    adapter = GeminiToolCallingModel(api_key="test", model="gemini-test", client=Client())

    with pytest.raises(ModelGatewayError) as failure:
        adapter.invoke([HumanMessage("Find notes")])

    assert str(failure.value) == "The Gemini tool-agent request could not be completed."
    assert "synthetic-secret" not in str(failure.value) and "C:/private" not in str(failure.value)


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
