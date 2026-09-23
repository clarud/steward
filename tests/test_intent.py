from datetime import UTC, datetime

import pytest

from steward.events import IncomingEvent
from steward.intent import Intent, IntentResolver


def event(text: str | None, attachments: tuple[str, ...] = ()) -> IncomingEvent:
    return IncomingEvent("telegram:1", "telegram", "1", "1", None, datetime(2026, 9, 8, tzinfo=UTC), text, attachments)


@pytest.mark.parametrize(("text", "intent"), [
    ("/save a note", Intent.CAPTURE), ("/delete this", Intent.UNKNOWN),
    ("/organize inbox", Intent.ORGANIZE), ("/inspect 4", Intent.INSPECT),
    ("What is a TLB?", Intent.ASK), ("hello", Intent.UNKNOWN),
    ("show me my upcoming events", Intent.ASK),
    ("summarize my CS3210 notes", Intent.ASK),
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


def test_resolver_recognizes_inbox_and_activity_without_question_punctuation() -> None:
    resolver = IntentResolver()

    inbox = resolver.resolve(event("what is in my inbox"))
    activity = resolver.resolve(event("show my recent activity"))

    assert inbox.primary_intent is Intent.INSPECT
    assert inbox.referenced_objects == ("inbox",)
    assert activity.primary_intent is Intent.INSPECT
    assert activity.referenced_objects == ("activity",)
