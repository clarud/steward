"""Declarative risk metadata and enforcement for Steward tools."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class ToolRisk(StrEnum):
    READ_ONLY = "read_only"
    SAFE_WRITE = "safe_write"
    SENSITIVE_WRITE = "sensitive_write"
    DESTRUCTIVE = "destructive"


@dataclass(frozen=True, slots=True)
class ToolDefinition:
    """Security-relevant facts the model itself must never be trusted to set."""

    name: str
    side_effects: bool
    risk: ToolRisk
    idempotency: str
    external_system: str | None
    requires_approval: bool

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("Tool definitions require a name.")
        if self.risk is ToolRisk.READ_ONLY and self.side_effects:
            raise ValueError("A read-only tool cannot declare side effects.")
        if self.risk is ToolRisk.READ_ONLY and self.requires_approval:
            raise ValueError("Read-only tools must not require approval.")
        if not self.idempotency.strip():
            raise ValueError("Tool definitions require an idempotency description.")


@dataclass(frozen=True, slots=True)
class ToolAuthorization:
    allowed: bool
    reason: str
    definition: ToolDefinition


class ToolPolicy:
    """Allow read-only calls and require explicit approval for side effects."""

    def __init__(self, definitions: list[ToolDefinition]) -> None:
        self._definitions = {definition.name: definition for definition in definitions}
        if len(self._definitions) != len(definitions):
            raise ValueError("Tool definitions must have unique names.")

    def definition_for(self, name: str) -> ToolDefinition:
        try:
            return self._definitions[name]
        except KeyError as error:
            raise ValueError(f"Tool {name!r} is not registered in the policy.") from error

    def authorize(self, name: str, *, approved: bool = False) -> ToolAuthorization:
        definition = self.definition_for(name)
        if definition.risk is ToolRisk.READ_ONLY:
            return ToolAuthorization(True, "Read-only tool permitted.", definition)
        if definition.requires_approval and not approved:
            return ToolAuthorization(False, "This tool requires explicit user approval.", definition)
        return ToolAuthorization(True, "Tool permitted by policy.", definition)
