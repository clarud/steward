"""Explicit registry for narrowly scoped external integrations."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from steward.tools import ToolDefinition


def oauth_token_readiness(
    token_path: Path,
    *,
    now: datetime | None = None,
    required_scopes: tuple[str, ...] = (),
    any_required_scope_sets: tuple[tuple[str, ...], ...] = (),
) -> str:
    """Return a secret-free local OAuth readiness summary.

    This is deliberately not an OAuth validation call: Telegram must neither
    send token data nor launch a browser. It only distinguishes absent,
    unreadable, expired-without-refresh, plausibly refreshable, and locally
    declared scope state. It never returns token values or scope names.
    """

    if not token_path.is_file():
        return "needs local browser authorization"
    try:
        payload = json.loads(token_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return "local token present (readiness cannot be verified)"
    if not isinstance(payload, dict):
        return "local token present (readiness cannot be verified)"
    # Refreshing a token does not repair missing authorization scopes. Check
    # required access even when expiry metadata is absent or malformed. Some
    # integrations intentionally accept either a read-only token or a broader
    # write token, so an alternative set can be supplied without revealing the
    # provider's scope strings in the result.
    if required_scopes or any_required_scope_sets:
        scopes = payload.get("scopes")
        if isinstance(scopes, str):
            granted_scopes = frozenset(scopes.split())
        elif isinstance(scopes, list) and all(isinstance(scope, str) for scope in scopes):
            granted_scopes = frozenset(scopes)
        else:
            return "local token present (required access cannot be verified)"
        has_required_scopes = bool(required_scopes) and set(required_scopes).issubset(granted_scopes)
        has_alternative_scope_set = bool(any_required_scope_sets) and any(
            set(scope_set).issubset(granted_scopes) for scope_set in any_required_scope_sets
        )
        if not has_required_scopes and not has_alternative_scope_set:
            return "local token lacks required access; reauthorize locally"
    expiry = payload.get("expiry")
    if not isinstance(expiry, str):
        return "local token present"
    try:
        expiry_at = datetime.fromisoformat(expiry.replace("Z", "+00:00"))
        if expiry_at.tzinfo is None:
            return "local token present (readiness cannot be verified)"
    except ValueError:
        return "local token present (readiness cannot be verified)"
    current = now or datetime.now(UTC)
    if expiry_at <= current:
        return (
            "local token expired; refresh available locally"
            if bool(payload.get("refresh_token"))
            else "local token expired; reauthorize locally"
        )
    return "local token present"


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
