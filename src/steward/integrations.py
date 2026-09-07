"""Explicit registry for narrowly scoped external integrations."""

from __future__ import annotations

from dataclasses import dataclass

from steward.tools import ToolDefinition


@dataclass(frozen=True, slots=True)
class IntegrationDefinition:
    name: str
    adapter: str
    service: str
    tool_names: tuple[str, ...]
    records_activity: bool


class IntegrationRegistry:
    """Reject unscoped or duplicate external integrations at composition time."""

    def __init__(self) -> None:
        self._definitions: dict[str, IntegrationDefinition] = {}

    def register(self, definition: IntegrationDefinition, tool_definitions: list[ToolDefinition]) -> None:
        if definition.name in self._definitions:
            raise ValueError(f"Integration {definition.name!r} is already registered.")
        declared = {tool.name for tool in tool_definitions if tool.external_system == definition.name}
        missing = set(definition.tool_names) - declared
        if missing:
            raise ValueError(f"Integration {definition.name!r} has unregistered tool policy: {sorted(missing)}")
        self._definitions[definition.name] = definition

    def list_all(self) -> tuple[IntegrationDefinition, ...]:
        return tuple(self._definitions.values())
