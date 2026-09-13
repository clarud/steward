"""Ollama adapter for Steward's explicit LangGraph ``ToolNode`` loop."""

from __future__ import annotations

import json
from typing import Any, Callable
from urllib import request
from uuid import uuid4

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import BaseTool

from steward.answer.gateway import ModelGatewayError


class OllamaToolCallingModel:
    """Translate LangGraph messages and tools to Ollama's local chat API.

    Ollama selects a tool call, but this adapter never executes it.  The custom
    LangGraph graph passes the returned ``AIMessage`` to ``ToolNode``, which is
    the single place where Steward's allowlisted tools run.
    """

    def __init__(
        self,
        *,
        model: str,
        base_url: str = "http://127.0.0.1:11434",
        timeout_seconds: int = 180,
        opener: Callable[..., Any] | None = None,
    ) -> None:
        if not model.strip():
            raise ValueError("An Ollama model name is required.")
        if timeout_seconds <= 0:
            raise ValueError("The Ollama timeout must be positive.")
        self._model = model
        self._url = f"{base_url.rstrip('/')}/api/chat"
        # Local CPU inference can take substantially longer than a cloud API,
        # particularly after a tool result lengthens the next prompt.
        self._timeout_seconds = timeout_seconds
        self._opener = opener or request.urlopen
        self._tools: list[BaseTool] = []

    def bind_tools(self, tools: list[BaseTool]) -> "OllamaToolCallingModel":
        self._tools = list(tools)
        return self

    def invoke(self, messages: list[BaseMessage]) -> AIMessage:
        """Request an Ollama assistant message or one or more tool calls."""
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": self._messages(messages),
            "stream": False,
        }
        if self._tools:
            payload["tools"] = [self._tool_schema(tool) for tool in self._tools]
        http_request = request.Request(
            self._url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with self._opener(http_request, timeout=self._timeout_seconds) as response:
                body = json.loads(response.read().decode("utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeDecodeError) as error:
            raise ModelGatewayError("The local Ollama tool-agent request could not be completed.") from error

        if not isinstance(body, dict):
            raise ModelGatewayError("The local Ollama model returned an invalid chat response.")
        message = body.get("message")
        if not isinstance(message, dict):
            raise ModelGatewayError("The local Ollama model returned an invalid chat response.")
        text = str(message.get("content") or "")
        tool_calls = self._tool_calls(message.get("tool_calls"))
        if tool_calls:
            return AIMessage(content=text, tool_calls=tool_calls)
        if not text.strip():
            return AIMessage(
                "I completed the available lookup, but the local model did not return a final answer. Please try again."
            )
        return AIMessage(content=text)

    @staticmethod
    def _tool_schema(tool: BaseTool) -> dict[str, Any]:
        schema = tool.args_schema.model_json_schema()
        parameters = {
            key: value
            for key, value in schema.items()
            if key in {"type", "properties", "required", "description"}
        }
        return {
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description or tool.name,
                "parameters": parameters,
            },
        }

    @staticmethod
    def _messages(messages: list[BaseMessage]) -> list[dict[str, Any]]:
        """Serialize only the roles in Steward's model/tool conversation."""
        serialized: list[dict[str, Any]] = []
        for message in messages:
            if isinstance(message, SystemMessage):
                serialized.append({"role": "system", "content": str(message.content)})
            elif isinstance(message, HumanMessage):
                serialized.append({"role": "user", "content": str(message.content)})
            elif isinstance(message, ToolMessage):
                serialized.append(
                    {
                        "role": "tool",
                        "tool_name": message.name or "tool",
                        "content": str(message.content),
                    }
                )
            elif isinstance(message, AIMessage):
                assistant_message: dict[str, Any] = {
                    "role": "assistant",
                    "content": str(message.content),
                }
                if message.tool_calls:
                    assistant_message["tool_calls"] = [
                        {
                            "type": "function",
                            "function": {
                                "index": index,
                                "name": call["name"],
                                "arguments": call["args"],
                            },
                        }
                        for index, call in enumerate(message.tool_calls)
                    ]
                serialized.append(assistant_message)
        return serialized

    @staticmethod
    def _tool_calls(raw_calls: object) -> list[dict[str, Any]]:
        if not isinstance(raw_calls, list):
            return []
        calls: list[dict[str, Any]] = []
        for index, raw_call in enumerate(raw_calls):
            function = raw_call.get("function") if isinstance(raw_call, dict) else None
            if not isinstance(function, dict):
                continue
            name = function.get("name")
            arguments = function.get("arguments", {})
            if not isinstance(name, str) or not name.strip() or not isinstance(arguments, dict):
                continue
            # Ollama does not return provider tool-call IDs. ToolNode needs one
            # internally to join its ToolMessage to this assistant message.
            calls.append({"name": name, "args": arguments, "id": f"ollama-{uuid4().hex}"})
        return calls
