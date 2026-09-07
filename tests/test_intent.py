from datetime import UTC, datetime

import pytest

from steward.events import IncomingEvent
from steward.intent import Intent, IntentResolver


def event(text: str | None, attachments: tuple[str, ...] = ()) -> IncomingEvent:
    return IncomingEvent("telegram:1", "telegram", "1", "1", None, datetime(2026, 9, 8, tzinfo=UTC), text, attachments)


@pytest.mark.parametrize(("text", "intent"), [
    ("/save a note", Intent.CAPTURE), ("/delete this", Intent.DELETE),
    ("/organize inbox", Intent.ORGANIZE), ("/inspect 4", Intent.INSPECT),
    ("What is a TLB?", Intent.ASK), ("hello", Intent.UNKNOWN),
])
def test_resolver_uses_deterministic_signals(text, intent) -> None:
    assert IntentResolver().resolve(event(text)).primary_intent is intent


def test_attachment_is_a_capture_signal() -> None:
    assert IntentResolver().resolve(event(None, ("document",))).primary_intent is Intent.CAPTURE


def test_reply_target_is_preserved_as_a_referenced_object() -> None:
    incoming = IncomingEvent(
        "telegram:8", "telegram", "100", "8", "7", datetime(2026, 9, 8, tzinfo=UTC), "Save this"
    )

    assert IntentResolver().resolve(incoming).referenced_objects == ("7",)
