"""The first explicit model → tool → model LangGraph loop."""

from __future__ import annotations

import json
from typing import Annotated, Callable, Literal, Protocol, TypedDict

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import BaseTool
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode

from steward.tools.policy import ToolPolicy
from steward.observability import trace
from steward.answer.gateway import ModelGatewayError


class ToolCallingModel(Protocol):
    """The small LangChain-compatible model surface required by this graph."""

    def bind_tools(self, tools: list[BaseTool]) -> "ToolCallingModel": ...

    def invoke(self, messages: list[BaseMessage]) -> AIMessage: ...


class ToolAgentState(TypedDict):
    """Append-only conversational state for a custom ToolNode loop."""

    messages: Annotated[list[BaseMessage], add_messages]


def build_tool_agent_graph(
    model: ToolCallingModel,
    tools: list[BaseTool],
    *,
    checkpointer: object | None = None,
    tool_policy: ToolPolicy | None = None,
    max_tool_calls: int = 4,
):
    """Compile a custom read-only tool loop without a prebuilt agent wrapper."""
    if not tools:
        raise ValueError("A tool agent requires at least one tool.")
    if max_tool_calls <= 0:
        raise ValueError("max_tool_calls must be positive.")
    bound_model = model.bind_tools(tools)

    def finish_from_evidence(state: ToolAgentState, fallback: str) -> dict[str, list[BaseMessage]]:
        """Allow one final synthesis, but never execute further requested tools."""
        trace("tool_agent.final_synthesis")
        try:
            response = bound_model.invoke([
                *state["messages"],
                SystemMessage(
                    "The tool budget is closed. Give your best final answer using only results already received. "
                    "Explain any missing evidence. Do not request more tools or claim unperformed actions."
                ),
            ])
        except ModelGatewayError:
            return {"messages": [AIMessage(fallback)]}
        if response.tool_calls or not response.content:
            return {"messages": [AIMessage(fallback)]}
        return {"messages": [response]}

    def call_model(state: ToolAgentState) -> dict[str, list[BaseMessage]]:
        last_request_index = max(
            (index for index, message in enumerate(state["messages"]) if isinstance(message, HumanMessage)),
            default=0,
        )
        tool_results = sum(
            isinstance(message, ToolMessage)
            for message in state["messages"][last_request_index + 1 :]
        )
        if tool_results >= max_tool_calls:
            trace("tool_agent.tool_budget_exhausted", max_tool_calls=max_tool_calls)
            return finish_from_evidence(
                state, "I reached Steward's tool-call limit before completing this request. "
                "Please narrow the question or start a new request.",
            )
        trace("tool_agent.model_call", message_count=len(state["messages"]))
        try:
            response = bound_model.invoke(state["messages"])
        except ModelGatewayError:
            return {
                "messages": [
                    AIMessage(
                        "The configured model is temporarily unavailable. Please retry later or use a local model."
                    )
                ]
            }
        remaining_calls = max_tool_calls - tool_results
        if len(response.tool_calls) > remaining_calls:
            # Providers can emit many calls in one assistant response.  The
            # pre-node guard above only sees prior ToolMessages, so enforce the
            # total here before ToolNode receives any of this batch.
            trace(
                "tool_agent.tool_budget_exhausted",
                max_tool_calls=max_tool_calls,
                requested_tool_calls=len(response.tool_calls),
            )
            return {
                "messages": [
                    AIMessage(
                        "I reached Steward's tool-call limit before completing this request. "
                        "Please narrow the question or start a new request."
                    )
                ]
            }
        previous_calls = {
            (
                call["name"],
                json.dumps(call.get("args", {}), sort_keys=True, separators=(",", ":"), default=str),
            )
            for message in state["messages"][last_request_index + 1 :]
            if isinstance(message, AIMessage)
            for call in message.tool_calls
        }
        requested_calls = {
            (
                call["name"],
                json.dumps(call.get("args", {}), sort_keys=True, separators=(",", ":"), default=str),
            )
            for call in response.tool_calls
        }
        if requested_calls and requested_calls.issubset(previous_calls):
            # Repeating an identical local lookup cannot add evidence. Some
            # smaller models do this after receiving a valid empty result, so
            # stop with a useful recovery message instead of wasting the whole
            # tool budget and eventually surfacing a graph recursion error.
            trace("tool_agent.repeated_tool_request", tool_names=sorted(call[0] for call in requested_calls))
            return {
                "messages": [
                    AIMessage(
                        "I already completed that same local lookup and it did not produce a new result. "
                        "Please name a different topic, source, or time range."
                    )
                ]
            }
        return {"messages": [response]}

    def route_after_model(state: ToolAgentState) -> Literal["tools", "end"]:
        latest = state["messages"][-1]
        route = "tools" if isinstance(latest, AIMessage) and latest.tool_calls else "end"
        trace("tool_agent.route", route=route)
        return route

    builder = StateGraph(ToolAgentState)
    builder.add_node("model", call_model)
    def enforce_policy(request, execute):
        trace("tool_agent.tool_request", tool_name=request.tool_call["name"])
        if tool_policy is None:
            return execute(request)
        authorization = tool_policy.authorize(request.tool_call["name"])
        if authorization.allowed:
            return execute(request)
        return ToolMessage(
            content=json.dumps({"error": authorization.reason, "risk": authorization.definition.risk.value}),
            name=request.tool_call["name"],
            tool_call_id=request.tool_call["id"],
        )

    builder.add_node("tools", ToolNode(tools, wrap_tool_call=enforce_policy))
    builder.add_edge(START, "model")
    builder.add_conditional_edges("model", route_after_model, {"tools": "tools", "end": END})
    builder.add_edge("tools", "model")
    return builder.compile(checkpointer=checkpointer)
