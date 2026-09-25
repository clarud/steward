"""Transport-neutral events entering the Steward application."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class IncomingEvent:
    """A normalized incoming message that Steward can handle independently."""

    id: str
    platform: str
    chat_id: str
    message_id: str
    reply_to_id: str | None
    timestamp: datetime
    text: str | None
    attachments: tuple[str, ...] = ()
    reply_text: str | None = None

    def __post_init__(self) -> None:
        if not all((self.id, self.platform, self.chat_id, self.message_id)):
            raise ValueError("Incoming events require stable platform identifiers.")
        if self.timestamp.tzinfo is None:
            raise ValueError("Incoming event timestamps must include a timezone.")
