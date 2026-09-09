import asyncio
from datetime import UTC, datetime
from pathlib import Path

import pytest
import steward.telegram.adapter as telegram_adapter

from steward.events import IncomingEvent
from steward.activity import ActivityService, ActivityType
from steward.storage import initialize_database
from steward.tasks import TaskReminderService, TaskService
from steward.telegram import (
    TelegramCallbackRepository,
    TelegramAdapter,
    TelegramUpdateDeliveryRepository,
    deliver_due_task_reminders,
    normalize_telegram_update,
    run_telegram_polling,
)


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


class FakeCallbackMessage(FakeMessage):
    async def reply_text(self, text: str, **kwargs) -> None:
        del kwargs
        self.replies.append(text)


class FakeCallbackQuery:
    def __init__(self, message: FakeCallbackMessage, data: str) -> None:
        self.id = "callback-42"
        self.message = message
        self.data = data
        self.answered = False

    async def answer(self) -> None:
        self.answered = True


class FakeCallbackUpdate(FakeUpdate):
    def __init__(self, message: FakeCallbackMessage, data: str) -> None:
        super().__init__(message)
        self.callback_query = FakeCallbackQuery(message, data)


class FakeEventHandler:
    def __init__(self) -> None:
        self.events: list[IncomingEvent] = []

    def handle(self, event: IncomingEvent) -> str:
        self.events.append(event)
        return "A TLB caches address translations. [F1]"


class FailingThenWorkingHandler(FakeEventHandler):
    def __init__(self) -> None:
        super().__init__()
        self._attempts = 0

    def handle(self, event: IncomingEvent) -> str:
        self._attempts += 1
        if self._attempts == 1:
            raise RuntimeError("temporary application failure")
        return super().handle(event)


class FakeBot:
    def __init__(self, *, fail: bool = False) -> None:
        self.sent: list[tuple[str, str]] = []
        self._fail = fail

    async def send_message(self, *, chat_id: str, text: str) -> None:
        if self._fail:
            raise RuntimeError("Telegram unavailable")
        self.sent.append((chat_id, text))


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
        reply_text="What is a TLB?",
    )


def test_adapter_delegates_normalized_event_and_replies() -> None:
    message = FakeMessage()
    update = FakeUpdate(message)
    handler = FakeEventHandler()

    asyncio.run(TelegramAdapter(handler).handle_update(update, None))  # type: ignore[arg-type]

    assert handler.events[0].id == "telegram:42"
    assert message.replies == ["A TLB caches address translations. [F1]"]


def test_adapter_resolves_a_chat_scoped_callback_to_a_local_command(tmp_path) -> None:
    database_path = tmp_path / "steward.db"
    initialize_database(database_path)
    callbacks = TelegramCallbackRepository(database_path)
    token = callbacks.create("100", "/inbox 2").token
    message = FakeCallbackMessage()
    update = FakeCallbackUpdate(message, token)
    handler = FakeEventHandler()

    asyncio.run(
        TelegramAdapter(handler, callback_repository=callbacks).handle_callback(update, None)  # type: ignore[arg-type]
    )

    assert update.callback_query.answered is True
    assert handler.events[0].text == "/inbox 2"
    assert message.replies == ["A TLB caches address translations. [F1]"]


def test_adapter_rejects_an_unknown_or_expired_callback(tmp_path) -> None:
    database_path = tmp_path / "steward.db"
    initialize_database(database_path)
    message = FakeCallbackMessage()
    update = FakeCallbackUpdate(message, "not-a-local-token")

    asyncio.run(
        TelegramAdapter(
            FakeEventHandler(), callback_repository=TelegramCallbackRepository(database_path)
        ).handle_callback(update, None)  # type: ignore[arg-type]
    )

    assert message.replies == ["This Steward action is invalid or has expired. Send /help."]


def test_adapter_ignores_a_delivered_duplicate_callback(tmp_path) -> None:
    database_path = tmp_path / "steward.db"
    initialize_database(database_path)
    callbacks = TelegramCallbackRepository(database_path)
    token = callbacks.create("100", "/approve_action 1").token
    deliveries = TelegramUpdateDeliveryRepository(database_path)
    handler = FakeEventHandler()
    adapter = TelegramAdapter(
        handler, callback_repository=callbacks, delivery_repository=deliveries
    )

    first_message = FakeCallbackMessage()
    asyncio.run(adapter.handle_callback(FakeCallbackUpdate(first_message, token), None))  # type: ignore[arg-type]
    duplicate_message = FakeCallbackMessage()
    asyncio.run(adapter.handle_callback(FakeCallbackUpdate(duplicate_message, token), None))  # type: ignore[arg-type]

    assert [event.text for event in handler.events] == ["/approve_action 1"]
    assert first_message.replies == ["A TLB caches address translations. [F1]"]
    assert duplicate_message.replies == []


def test_adapter_ignores_a_delivered_duplicate_update(tmp_path) -> None:
    database_path = tmp_path / "steward.db"
    initialize_database(database_path)
    handler = FakeEventHandler()
    adapter = TelegramAdapter(
        handler, delivery_repository=TelegramUpdateDeliveryRepository(database_path)
    )

    asyncio.run(adapter.handle_update(FakeUpdate(FakeMessage()), None))  # type: ignore[arg-type]
    duplicate_message = FakeMessage()
    asyncio.run(adapter.handle_update(FakeUpdate(duplicate_message), None))  # type: ignore[arg-type]

    assert len(handler.events) == 1
    assert duplicate_message.replies == []


def test_adapter_defers_an_immediate_retry_after_a_failure(tmp_path) -> None:
    database_path = tmp_path / "steward.db"
    initialize_database(database_path)
    handler = FailingThenWorkingHandler()
    adapter = TelegramAdapter(
        handler, delivery_repository=TelegramUpdateDeliveryRepository(database_path)
    )

    with pytest.raises(RuntimeError, match="temporary application failure"):
        asyncio.run(adapter.handle_update(FakeUpdate(FakeMessage()), None))  # type: ignore[arg-type]
    retried_message = FakeMessage()
    asyncio.run(adapter.handle_update(FakeUpdate(retried_message), None))  # type: ignore[arg-type]

    assert handler.events == []
    assert retried_message.replies == []


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


def test_polling_registers_a_fallback_for_unknown_commands(monkeypatch) -> None:
    class FakeApplication:
        def __init__(self) -> None: self.handlers: list[object] = []
        def add_handler(self, handler: object) -> None: self.handlers.append(handler)
        def run_polling(self) -> None: pass

    application = FakeApplication()

    class FakeBuilder:
        def token(self, _token: str): return self
        def build(self) -> FakeApplication: return application

    monkeypatch.setattr(telegram_adapter, "ApplicationBuilder", lambda: FakeBuilder())
    monkeypatch.setattr(telegram_adapter, "CommandHandler", lambda command, _callback: ("command", command))
    monkeypatch.setattr(telegram_adapter, "MessageHandler", lambda selected_filter, _callback: ("message", str(selected_filter)))
    monkeypatch.setattr(telegram_adapter, "CallbackQueryHandler", lambda _callback: ("callback",))

    run_telegram_polling("token", FakeEventHandler(), FakeEventHandler())

    command_positions = [index for index, handler in enumerate(application.handlers) if handler == ("command", "save")]
    unregister_positions = [
        index for index, handler in enumerate(application.handlers)
        if handler == ("command", "propose_unregister_source")
    ]
    fallback_positions = [
        index for index, handler in enumerate(application.handlers)
        if handler == ("message", str(telegram_adapter.filters.COMMAND))
    ]
    assert command_positions and unregister_positions and fallback_positions
    assert command_positions[0] < fallback_positions[0]
    assert unregister_positions[0] < fallback_positions[0]


def test_due_task_reminders_are_acknowledged_only_after_telegram_accepts_them(tmp_path: Path) -> None:
    database = tmp_path / "steward.db"; initialize_database(database)
    activity = ActivityService(database); tasks = TaskService(database)
    reminder_service = TaskReminderService(database, tasks, activity)
    task = tasks.create("Submit CS3210 lab")
    reminder_service.schedule(task.id or 0, "100", datetime(2020, 1, 1, tzinfo=UTC))

    bot = FakeBot()

    assert asyncio.run(deliver_due_task_reminders(bot, reminder_service)) == 1
    assert bot.sent == [("100", "Reminder: Task 1: Submit CS3210 lab\nScheduled for: 2020-01-01T00:00:00+00:00")]
    assert asyncio.run(deliver_due_task_reminders(bot, reminder_service)) == 0
    assert activity.list_recent()[0].event_type is ActivityType.TASK_REMINDER_SENT


def test_failed_task_reminder_delivery_releases_the_claim_for_retry(tmp_path: Path) -> None:
    database = tmp_path / "steward.db"; initialize_database(database)
    activity = ActivityService(database); tasks = TaskService(database)
    reminder_service = TaskReminderService(database, tasks, activity)
    task = tasks.create("Submit CS3210 lab")
    reminder_service.schedule(task.id or 0, "100", datetime(2020, 1, 1, tzinfo=UTC))

    assert asyncio.run(deliver_due_task_reminders(FakeBot(fail=True), reminder_service)) == 0
    retry_bot = FakeBot()
    assert asyncio.run(deliver_due_task_reminders(retry_bot, reminder_service)) == 1


def test_document_over_cloud_limit_is_not_downloaded() -> None:
    message = FakeMessage()
    message.caption = "/save"
    message.document = type("Document", (), {"file_size": 21 * 1024 * 1024})()

    asyncio.run(TelegramAdapter(FakeEventHandler()).handle_document(FakeUpdate(message), None))  # type: ignore[arg-type]

    assert "over 20 MB" in message.replies[0]
    assert "/drive_import DRIVE_FILE_ID" in message.replies[0]


def test_adapter_ignores_a_delivered_duplicate_document_update(tmp_path) -> None:
    class Download:
        async def download_to_drive(self, path: Path) -> None:
            path.write_text("# OpenMP", encoding="utf-8")

    class Document:
        file_size = 32
        file_name = "notes.md"

        async def get_file(self) -> Download:
            return Download()

    class FileHandler(FakeEventHandler):
        def __init__(self) -> None:
            super().__init__()
            self.files: list[tuple[IncomingEvent, str]] = []

        def handle_file(self, event: IncomingEvent, path: Path) -> str:
            self.files.append((event, path.read_text(encoding="utf-8")))
            return "Staged document"

    database = tmp_path / "steward.db"
    initialize_database(database)
    handler = FileHandler()
    adapter = TelegramAdapter(handler, delivery_repository=TelegramUpdateDeliveryRepository(database))
    first_message = FakeMessage(); first_message.document = Document()
    duplicate_message = FakeMessage(); duplicate_message.document = Document()

    asyncio.run(adapter.handle_document(FakeUpdate(first_message), None))  # type: ignore[arg-type]
    asyncio.run(adapter.handle_document(FakeUpdate(duplicate_message), None))  # type: ignore[arg-type]

    assert len(handler.files) == 1
    assert handler.files[0][0].id == "telegram:42"
    assert handler.files[0][1] == "# OpenMP"
    assert first_message.replies == ["Staged document"]
    assert duplicate_message.replies == []


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
