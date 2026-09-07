import asyncio
from datetime import UTC, datetime

import pytest

from steward.events import IncomingEvent
from steward.telegram import TelegramAdapter, normalize_telegram_update, run_telegram_polling


class FakeMessage:
    def __init__(self, *, text: str = "What is a TLB?", reply_to_message=None) -> None:
        self.chat_id = 100
        self.message_id = 7
        self.reply_to_message = reply_to_message
        self.date = datetime(2026, 9, 7, tzinfo=UTC)
        self.text = text
        self.replies: list[str] = []

    async def reply_text(self, text: str) -> None:
        self.replies.append(text)


class FakeUpdate:
    def __init__(self, message: FakeMessage) -> None:
        self.update_id = 42
        self.effective_message = message


class FakeEventHandler:
    def __init__(self) -> None:
        self.events: list[IncomingEvent] = []

    def handle(self, event: IncomingEvent) -> str:
        self.events.append(event)
        return "A TLB caches address translations. [F1]"


def test_normalize_telegram_update_preserves_reply_relationship() -> None:
    original = FakeMessage()
    update = FakeUpdate(FakeMessage(reply_to_message=original))

    event = normalize_telegram_update(update)  # type: ignore[arg-type]

    assert event == IncomingEvent(
        id="telegram:42",
        platform="telegram",
        chat_id="100",
        message_id="7",
        reply_to_id="7",
        timestamp=datetime(2026, 9, 7, tzinfo=UTC),
        text="What is a TLB?",
    )


def test_adapter_delegates_normalized_event_and_replies() -> None:
    message = FakeMessage()
    update = FakeUpdate(message)
    handler = FakeEventHandler()

    asyncio.run(TelegramAdapter(handler).handle_update(update, None))  # type: ignore[arg-type]

    assert handler.events[0].id == "telegram:42"
    assert message.replies == ["A TLB caches address translations. [F1]"]


def test_polling_rejects_empty_token() -> None:
    with pytest.raises(ValueError, match="must not be empty"):
        run_telegram_polling("   ", FakeEventHandler())
