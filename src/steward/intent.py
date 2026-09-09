"""Deterministic first-pass interpretation of incoming Steward requests."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from steward.events import IncomingEvent


class Intent(StrEnum):
    ASK = "ask"
    CAPTURE = "capture"
    SEARCH = "search"
    ORGANIZE = "organize"
    CREATE_WORKSPACE = "create_workspace"
    DELETE = "delete"
    INSPECT = "inspect"
    ACTION = "action"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class IntentDecision:
    primary_intent: Intent
    secondary_intents: tuple[Intent, ...] = ()
    referenced_objects: tuple[str, ...] = ()
    confidence: str = "deterministic"


class IntentResolver:
    """Resolve unambiguous transport signals before asking a model later."""

    def resolve(self, event: IncomingEvent) -> IntentDecision:
        text = (event.text or "").strip()
        command = text.split(maxsplit=1)[0].casefold() if text else ""
        normalized = " ".join(text.casefold().split())
        referenced_objects = (event.reply_to_id,) if event.reply_to_id is not None else ()
        mapping = {
            "/save": Intent.CAPTURE,
            "/delete": Intent.DELETE,
            "/organize": Intent.ORGANIZE,
            "/inspect": Intent.INSPECT,
        }
        if command in mapping:
            return IntentDecision(mapping[command], referenced_objects=referenced_objects)
        if event.attachments:
            return IntentDecision(Intent.CAPTURE, referenced_objects=referenced_objects)
        if normalized in {"what is in my inbox", "what is in inbox", "show my inbox", "show inbox"}:
            return IntentDecision(Intent.INSPECT, referenced_objects=("inbox", *referenced_objects))
        if normalized.startswith(("show my recent activity", "show recent activity", "what happened")):
            return IntentDecision(Intent.INSPECT, referenced_objects=("activity", *referenced_objects))
        if normalized.startswith(("find ", "search ", "look for ")):
            return IntentDecision(Intent.SEARCH, referenced_objects=referenced_objects)
        if normalized.startswith(("organize ", "sort ")):
            return IntentDecision(Intent.ORGANIZE, referenced_objects=referenced_objects)
        if normalized.startswith(("create a workspace", "create workspace", "new workspace")):
            return IntentDecision(Intent.CREATE_WORKSPACE, referenced_objects=referenced_objects)
        if text.endswith("?") or normalized.startswith(
            ("what ", "where ", "when ", "who ", "why ", "how ", "do i ", "can i ")
        ):
            return IntentDecision(Intent.ASK, referenced_objects=referenced_objects)
        return IntentDecision(Intent.UNKNOWN, referenced_objects=referenced_objects, confidence="unresolved")
