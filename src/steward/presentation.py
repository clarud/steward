"""Transport-neutral presentation details for interactive Steward replies."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from steward.sources.export import OriginalDocument


def timestamp_label(value: datetime) -> str:
    """Render one timezone-aware timestamp without silently changing its zone."""

    if value.tzinfo is None or value.utcoffset() is None:
        return value.isoformat()
    offset = value.strftime("%z")
    zone = f"UTC{offset[:3]}:{offset[3:]}" if offset else "timezone unspecified"
    clock = f"{value.hour % 12 or 12}:{value.minute:02d} {'am' if value.hour < 12 else 'pm'}"
    return f"{value.day} {value:%b %Y} · {clock} ({zone})"


@dataclass(frozen=True, slots=True)
class ReplyAction:
    """One explicitly permitted follow-up action presented by a transport."""

    label: str
    command: str


@dataclass(frozen=True, slots=True)
class PresentedReply:
    """Text plus bounded follow-up actions; not a domain mutation instruction."""

    text: str
    actions: tuple[ReplyAction, ...] = ()
    title: str | None = None
    icon: str | None = None
    document: OriginalDocument | None = None
    reference: tuple[str, int | str] | None = None
