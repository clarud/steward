"""Transport-neutral presentation details for interactive Steward replies."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta


def calendar_time_label(start: str, end: str) -> str:
    """Display provider dates without guessing a timezone; all-day end is exclusive."""
    def day(value: date) -> str:
        return f"{value.day} {value:%b %Y}"

    def clock(value: datetime) -> str:
        return f"{value.hour % 12 or 12}:{value.minute:02d} {'am' if value.hour < 12 else 'pm'}"

    def zone(value: datetime) -> str:
        offset = value.strftime("%z")
        return f"UTC{offset[:3]}:{offset[3:]}" if offset else "timezone unspecified"

    try:
        if len(start) == len(end) == 10:
            first, exclusive_end = date.fromisoformat(start), date.fromisoformat(end)
            if exclusive_end <= first:
                return f"{start} → {end}"
            last = exclusive_end - timedelta(days=1)
            dates = day(first) if first == last else f"{day(first)} – {day(last)}"
            return f"{dates} · All day"
        first, last = datetime.fromisoformat(start), datetime.fromisoformat(end)
        if first.date() == last.date() and first.utcoffset() == last.utcoffset():
            return f"{day(first)} · {clock(first)}–{clock(last)} ({zone(first)})"
        return f"{day(first)} · {clock(first)} ({zone(first)})\n→ {day(last)} · {clock(last)} ({zone(last)})"
    except ValueError:
        return f"{start} → {end}"


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
