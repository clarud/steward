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
        self.caption = None
        self.document = None
        self.photo = ()
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


def test_adapter_rejects_an_unauthorized_chat_without_calling_steward() -> None:
    message = FakeMessage()
    handler = FakeEventHandler()

    asyncio.run(
        TelegramAdapter(handler, allowed_chat_ids=frozenset({"999"})).handle_update(
            FakeUpdate(message), None
        )
    )  # type: ignore[arg-type]

    assert handler.events == []
    assert message.replies == ["This Steward bot is not authorized for this chat."]


def test_polling_rejects_empty_token() -> None:
    with pytest.raises(ValueError, match="must not be empty"):
        run_telegram_polling("   ", FakeEventHandler(), FakeEventHandler())


def test_document_over_cloud_limit_is_not_downloaded() -> None:
    message = FakeMessage()
    message.caption = "/save"
    message.document = type("Document", (), {"file_size": 21 * 1024 * 1024})()

    asyncio.run(TelegramAdapter(FakeEventHandler()).handle_document(FakeUpdate(message), None))  # type: ignore[arg-type]

    assert "over 20 MB" in message.replies[0]


def test_normalize_telegram_update_assigns_a_safe_photo_attachment_name() -> None:
    message = FakeMessage()
    message.caption = "/save"
    message.photo = (object(),)

    event = normalize_telegram_update(FakeUpdate(message))  # type: ignore[arg-type]

    assert event.attachments == ("telegram-photo-7.jpg",)


def test_photo_over_cloud_limit_is_not_downloaded() -> None:
    message = FakeMessage()
    message.caption = "/save"
    message.photo = (type("Photo", (), {"file_size": 21 * 1024 * 1024})(),)

    asyncio.run(TelegramAdapter(FakeEventHandler()).handle_photo(FakeUpdate(message), None))  # type: ignore[arg-type]

    assert "over 20 MB" in message.replies[0]
