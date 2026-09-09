"""OpenAI-compatible adapter for Steward's explicit LangGraph tool loop."""

from __future__ import annotations

import json
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import BaseTool

from steward.answer.gateway import ModelGatewayError


class OpenAICompatibleToolCallingModel:
    """Send client-executed function tools to an OpenAI-compatible endpoint.

    The remote service proposes a function call only.  LangGraph's local
    ``ToolNode`` remains the sole executor of Steward's allowlisted tools.
    """

    def __init__(
        self, *, api_key: str, model: str, base_url: str, client: object | None = None
    ) -> None:
        if not api_key.strip():
            raise ValueError("An API key is required for the OpenAI-compatible provider.")
        if not model.strip():
            raise ValueError("An OpenAI-compatible model name is required.")
        if not base_url.strip():
            raise ValueError("An OpenAI-compatible base URL is required.")
        if client is None:
            from openai import OpenAI

            client = OpenAI(api_key=api_key, base_url=base_url)
        self._client = client
        self._model = model
        self._tools: list[BaseTool] = []

    def bind_tools(self, tools: list[BaseTool]) -> "OpenAICompatibleToolCallingModel":
        self._tools = list(tools)
        return self

    def invoke(self, messages: list[BaseMessage]) -> AIMessage:
        payload: dict[str, Any] = {"model": self._model, "messages": self._messages(messages)}
        if self._tools:
            payload["tools"] = [self._tool_schema(tool) for tool in self._tools]
        try:
            response = self._client.chat.completions.create(**payload)
        except Exception as error:
            raise ModelGatewayError("The OpenAI-compatible tool-agent request could not be completed.") from error

        choices = getattr(response, "choices", ())
        message = getattr(choices[0], "message", None) if choices else None
        if message is None:
            raise ModelGatewayError("The OpenAI-compatible provider returned no assistant message.")
        calls = self._tool_calls(getattr(message, "tool_calls", None))
        text = str(getattr(message, "content", "") or "")
        if calls:
            return AIMessage(content=text, tool_calls=calls)
        if not text.strip():
            return AIMessage(
                "I completed the available lookup, but the provider did not return a final answer. Please try again."
            )
        return AIMessage(content=text)

    @staticmethod
    def _tool_schema(tool: BaseTool) -> dict[str, Any]:
        schema = tool.args_schema.model_json_schema()
        return {
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description or tool.name,
                "parameters": {
                    key: value
                    for key, value in schema.items()
                    if key in {"type", "properties", "required", "description"}
                },
            },
        }

    @staticmethod
    def _messages(messages: list[BaseMessage]) -> list[dict[str, Any]]:
        serialized: list[dict[str, Any]] = []
        for message in messages:
            if isinstance(message, SystemMessage):
                serialized.append({"role": "system", "content": str(message.content)})
            elif isinstance(message, HumanMessage):
                serialized.append({"role": "user", "content": str(message.content)})
            elif isinstance(message, AIMessage):
                item: dict[str, Any] = {"role": "assistant", "content": str(message.content)}
                if message.tool_calls:
                    item["tool_calls"] = [
                        {
                            "id": call["id"],
                            "type": "function",
                            "function": {
                                "name": call["name"],
                                "arguments": json.dumps(call["args"]),
                            },
                        }
                        for call in message.tool_calls
                    ]
                serialized.append(item)
            elif isinstance(message, ToolMessage):
                serialized.append(
                    {
                        "role": "tool",
                        "tool_call_id": message.tool_call_id,
                        "content": str(message.content),
                    }
                )
        return serialized

    @staticmethod
    def _tool_calls(raw_calls: object) -> list[dict[str, Any]]:
        calls: list[dict[str, Any]] = []
        for raw_call in raw_calls or ():
            function = getattr(raw_call, "function", None)
            name = getattr(function, "name", None)
            arguments = getattr(function, "arguments", "{}")
            call_id = getattr(raw_call, "id", None)
            if not isinstance(name, str) or not name.strip() or not isinstance(call_id, str):
                continue
            try:
                parsed_arguments = json.loads(arguments) if isinstance(arguments, str) else arguments
            except json.JSONDecodeError:
                continue
            if isinstance(parsed_arguments, dict):
                calls.append({"name": name, "args": parsed_arguments, "id": call_id})
        return calls
