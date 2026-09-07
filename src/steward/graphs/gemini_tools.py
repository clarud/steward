"""Gemini adapter for Steward's explicit LangGraph ToolNode loop."""

from __future__ import annotations

from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import BaseTool


class GeminiToolCallingModel:
    """Map Gemini function calling onto the small ToolCallingModel protocol.

    Gemini decides whether to request a function. LangGraph's ToolNode, rather
    than the provider SDK, then executes only the tools Steward supplied.
    """

    def __init__(self, *, api_key: str, model: str, client: object | None = None) -> None:
        if not api_key.strip():
            raise ValueError("A Gemini API key is required.")
        if not model.strip():
            raise ValueError("A Gemini model name is required.")
        if client is None:
            from google import genai

            client = genai.Client(api_key=api_key)
        self._client = client
        self._model = model
        self._tools: list[BaseTool] = []

    def bind_tools(self, tools: list[BaseTool]) -> "GeminiToolCallingModel":
        self._tools = list(tools)
        return self

    def invoke(self, messages: list[BaseMessage]) -> AIMessage:
        """Ask Gemini for text or tool calls while preserving conversation history."""
        from google.genai import types

        declarations = [
            types.FunctionDeclaration(
                name=tool.name,
                description=tool.description or tool.name,
                parameters=self._parameters(tool),
            )
            for tool in self._tools
        ]
        system_parts = [str(message.content) for message in messages if isinstance(message, SystemMessage)]
        config = types.GenerateContentConfig(
            tools=[types.Tool(function_declarations=declarations)],
            system_instruction="\n".join(system_parts) or None,
            automatic_function_calling={"disable": True},
        )
        response = self._client.models.generate_content(
            model=self._model,
            contents=self._contents(messages, types),
            config=config,
        )
        calls = self._function_calls(response)
        if calls:
            return AIMessage(
                content=getattr(response, "text", "") or "",
                tool_calls=[
                    {
                        "name": str(call.name),
                        "args": dict(call.args or {}),
                        "id": str(call.id or f"gemini-{index}"),
                    }
                    for index, call in enumerate(calls)
                ],
            )
        text = getattr(response, "text", "") or ""
        if not text.strip():
            raise RuntimeError("Gemini returned neither text nor a tool call.")
        return AIMessage(content=text)

    @staticmethod
    def _parameters(tool: BaseTool) -> dict[str, Any]:
        schema = tool.args_schema.model_json_schema()
        return {
            key: value
            for key, value in schema.items()
            if key in {"type", "properties", "required", "description"}
        }

    @staticmethod
    def _contents(messages: list[BaseMessage], types: Any) -> list[Any]:
        contents: list[Any] = []
        for message in messages:
            if isinstance(message, SystemMessage):
                continue
            if isinstance(message, HumanMessage):
                contents.append(types.Content(role="user", parts=[types.Part(text=str(message.content))]))
            elif isinstance(message, ToolMessage):
                contents.append(
                    types.Content(
                        role="user",
                        parts=[
                            types.Part.from_function_response(
                                name=message.name or "tool",
                                response={"result": str(message.content)},
                                id=message.tool_call_id,
                            )
                        ],
                    )
                )
            elif isinstance(message, AIMessage):
                parts = [types.Part(text=str(message.content))] if message.content else []
                parts.extend(
                    types.Part.from_function_call(
                        name=call["name"], args=call["args"], id=call["id"]
                    )
                    for call in message.tool_calls
                )
                contents.append(types.Content(role="model", parts=parts))
        return contents

    @staticmethod
    def _function_calls(response: object) -> list[Any]:
        direct = getattr(response, "function_calls", None)
        if direct:
            return list(direct)
        candidates = getattr(response, "candidates", None) or []
        if not candidates:
            return []
        parts = getattr(getattr(candidates[0], "content", None), "parts", None) or []
        return [part.function_call for part in parts if getattr(part, "function_call", None) is not None]
