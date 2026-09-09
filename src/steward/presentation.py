"""Transport-neutral presentation details for interactive Steward replies."""

from __future__ import annotations

from dataclasses import dataclass


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
