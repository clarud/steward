"""Responses API adapter for Steward's explicit LangGraph tool loop."""

from __future__ import annotations

import json
import logging
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import BaseTool

from steward.answer.gateway import ModelGatewayError


logger = logging.getLogger(__name__)


class OpenAICompatibleToolCallingModel:
    """Adapt SoCLaaS's Responses API client-executed function tools.

    SoCLaaS returns function-call *requests* through ``/v1/responses``. This
    adapter turns them into LangChain ``AIMessage.tool_calls``; LangGraph's
    local ``ToolNode`` remains the only executor of Steward tools.
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
        payload: dict[str, Any] = {
            "model": self._model,
            "input": self._input(messages),
            # LangGraph persists Steward's conversation state locally; no
            # provider-side state must be retained to continue this loop.
            "store": False,
        }
        instructions = self._instructions(messages)
        if instructions:
            # SoCLaaS's Responses shim accepts system guidance through the
            # documented ``instructions`` field more reliably than a system
            # message embedded in the chat-style input history.
            payload["instructions"] = instructions
        if self._tools:
            payload["tools"] = [self._tool_schema(tool) for tool in self._tools]
        try:
            response = self._client.responses.create(**payload)
        except Exception as error:
            logger.warning(
                "OpenAI-compatible tool request failed (%s).", type(error).__name__
            )
            raise ModelGatewayError("The OpenAI-compatible tool-agent request could not be completed.") from error

        output = getattr(response, "output", ())
        calls = self._tool_calls(output)
        text = self._output_text(output, getattr(response, "output_text", ""))
        if calls:
            return AIMessage(content=text, tool_calls=calls)
        if not text:
            return AIMessage(
                "I completed the available lookup, but the provider did not return a final answer. Please try again."
            )
        return AIMessage(content=text)

    @staticmethod
    def _tool_schema(tool: BaseTool) -> dict[str, Any]:
        schema = tool.args_schema.model_json_schema()
        return {
            "type": "function",
            "name": tool.name,
            "description": tool.description or tool.name,
            "parameters": {
                key: value
                for key, value in schema.items()
                if key in {"type", "properties", "required", "description"}
            },
        }

    @staticmethod
    def _input(messages: list[BaseMessage]) -> list[dict[str, Any]]:
        """Translate LangGraph messages to stateless Responses API input."""
        serialized: list[dict[str, Any]] = []
        for message in messages:
            if isinstance(message, HumanMessage):
                serialized.append({"role": "user", "content": str(message.content)})
            elif isinstance(message, AIMessage):
                if message.content:
                    serialized.append({"role": "assistant", "content": str(message.content)})
                serialized.extend(
                    {
                        "type": "function_call",
                        "call_id": call["id"],
                        "name": call["name"],
                        "arguments": json.dumps(call["args"]),
                    }
                    for call in message.tool_calls
                )
            elif isinstance(message, ToolMessage):
                serialized.append(
                    {
                        "type": "function_call_output",
                        "call_id": message.tool_call_id,
                        "output": str(message.content),
                    }
                )
        return serialized

    @staticmethod
    def _instructions(messages: list[BaseMessage]) -> str:
        """Combine LangGraph system messages for the Responses instructions field."""
        return "\n\n".join(
            str(message.content).strip()
            for message in messages
            if isinstance(message, SystemMessage) and str(message.content).strip()
        )

    @staticmethod
    def _tool_calls(raw_output: object) -> list[dict[str, Any]]:
        calls: list[dict[str, Any]] = []
        for item in raw_output or ():
            if OpenAICompatibleToolCallingModel._field(item, "type") != "function_call":
                continue
            name = OpenAICompatibleToolCallingModel._field(item, "name")
            arguments = OpenAICompatibleToolCallingModel._field(item, "arguments", "{}")
            # Responses uses call_id, while some compatible shims use id.
            call_id = (
                OpenAICompatibleToolCallingModel._field(item, "call_id")
                or OpenAICompatibleToolCallingModel._field(item, "id")
            )
            if not isinstance(name, str) or not name.strip() or not isinstance(call_id, str):
                continue
            try:
                parsed_arguments = json.loads(arguments) if isinstance(arguments, str) else arguments
            except json.JSONDecodeError:
                continue
            if isinstance(parsed_arguments, dict):
                calls.append({"name": name, "args": parsed_arguments, "id": call_id})
        return calls

    @staticmethod
    def _field(item: object, name: str, default: object | None = None) -> object | None:
        """Read either SDK attribute objects or JSON-like shim response items."""
        if isinstance(item, dict):
            return item.get(name, default)
        return getattr(item, name, default)

    @classmethod
    def _output_text(cls, raw_output: object, convenience_text: object) -> str:
        """Recover a final text answer when a compatible shim omits ``output_text``.

        Function calls remain separate from text.  This only reads documented
        message/output-text content shapes and never invents an answer from a
        tool request or raw provider diagnostic.
        """
        text = str(convenience_text or "").strip()
        if text:
            return text
        extracted: list[str] = []
        for item in raw_output or ():
            if cls._field(item, "type") not in {"message", "output_text"}:
                continue
            content = cls._field(item, "content")
            parts = content if isinstance(content, (list, tuple)) else (item,)
            for part in parts:
                if cls._field(part, "type") not in {"output_text", "text", "message"}:
                    continue
                value = cls._field(part, "text") or cls._field(part, "value")
                if isinstance(value, str) and value.strip():
                    extracted.append(value.strip())
        return "\n".join(extracted)
