"""Telegram long-polling adapter for questions and explicit file capture."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Protocol

from telegram import BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ApplicationBuilder, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters

from steward.events import IncomingEvent
from steward.presentation import PresentedReply
from steward.telegram.presentation import TelegramPresenter
from steward.reviews import ReviewContextRepository
from steward.tasks import TaskReminderService
from steward.telegram.callbacks import TelegramCallbackRepository
from steward.telegram.delivery import TelegramUpdateDeliveryRepository


class IncomingEventHandler(Protocol):
    """Application boundary invoked after a platform update is normalized."""

    def handle(self, event: IncomingEvent) -> str | PresentedReply: ...


class IncomingFileEventHandler(IncomingEventHandler, Protocol):
    """Application boundary for an attachment downloaded by this adapter."""

    def handle_file(self, event: IncomingEvent, original_path: Path) -> str: ...


MAX_CLOUD_DOWNLOAD_BYTES = 20 * 1024 * 1024
_LOGGER = logging.getLogger(__name__)
_PRIMARY_COMMANDS = (
    ("home", "review what needs your decision"),
    ("pending", "show pending reviews"),
    ("search", "search your saved material"),
    ("calendar", "show current calendar events"),
    ("tasks", "show open tasks"),
    ("records", "show saved records"),
    ("inbox", "show saved Inbox items"),
    ("workspaces", "show workspaces"),
    ("organize", "review Inbox organization"),
    ("help", "show more options"),
)


def normalize_telegram_update(update: Update) -> IncomingEvent:
    """Convert one Telegram text update into Steward's internal event shape."""

    message = update.effective_message
    if message is None:
        raise ValueError("Telegram update does not contain a message.")
    if update.update_id is None:
        raise ValueError("Telegram update does not contain an update ID.")

    reply_to_id = (
        str(message.reply_to_message.message_id)
        if message.reply_to_message is not None
        else None
    )
    reply_text = (
        message.reply_to_message.text or message.reply_to_message.caption
        if message.reply_to_message is not None
        else None
    )
    return IncomingEvent(
        id=f"telegram:{update.update_id}",
        platform="telegram",
        chat_id=str(message.chat_id),
        message_id=str(message.message_id),
        reply_to_id=reply_to_id,
        timestamp=message.date,
        text=message.text or message.caption,
        attachments=_attachment_names(message),
        reply_text=reply_text,
    )


class TelegramAdapter:
    """Translate Telegram updates, delegate work, and send the returned text."""

    def __init__(
        self,
        event_handler: IncomingEventHandler,
        *,
        allowed_chat_ids: frozenset[str] = frozenset(),
        delivery_repository: TelegramUpdateDeliveryRepository | None = None,
        callback_repository: TelegramCallbackRepository | None = None,
        review_contexts: ReviewContextRepository | None = None,
    ) -> None:
        self._event_handler = event_handler
        self._allowed_chat_ids = allowed_chat_ids
        self._deliveries = delivery_repository
        self._callbacks = callback_repository
        self._review_contexts = review_contexts
        self._presenter = TelegramPresenter()

    async def handle_update(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Handle one text update without blocking Telegram's event loop."""

        del context
        event = normalize_telegram_update(update)
        message = update.effective_message
        if message is None:
            raise ValueError("Telegram update does not contain a message.")
        if not self._is_allowed(event):
            await message.reply_text("This Steward bot is not authorized for this chat.")
            return
        if not self._claim(event):
            return
        try:
            response = await asyncio.to_thread(self._event_handler.handle, event)
            await self._reply(message, event, response)
        except BaseException:
            self._release(event)
            raise
        self._mark_delivered(event)

    async def handle_document(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Download an explicitly saved document, then delegate preservation."""

        message = update.effective_message
        if message is None or message.document is None:
            raise ValueError("Telegram update does not contain a document.")
        await self._handle_attachment(
            update,
            context,
            message.document,
            getattr(message.document, "file_name", None) or "attachment.bin",
        )

    async def handle_photo(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Download an explicitly saved Telegram photo, then delegate preservation."""

        message = update.effective_message
        if message is None or not message.photo:
            raise ValueError("Telegram update does not contain a photo.")
        await self._handle_attachment(
            update,
            context,
            message.photo[-1],
            f"telegram-photo-{message.message_id}.jpg",
        )

    async def _handle_attachment(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE, attachment: object, filename: str
    ) -> None:
        """Download one explicitly saved attachment and send the application's response."""

        del context
        message = update.effective_message
        if message is None:
            raise ValueError("Telegram update does not contain a message.")
        event = normalize_telegram_update(update)
        if not self._is_allowed(event):
            await message.reply_text("This Steward bot is not authorized for this chat.")
            return
        file_size = getattr(attachment, "file_size", None)
        if file_size and file_size > MAX_CLOUD_DOWNLOAD_BYTES:
            await message.reply_text(
                "I cannot download files over 20 MB through the current Telegram connection. "
                "Place the original in vault/inbox, or upload it to Google Drive and send "
                "/drive_import DRIVE_FILE_ID."
            )
            return
        if not hasattr(self._event_handler, "handle_file"):
            raise TypeError("Attachment handling requires a file capture application.")
        if not self._claim(event):
            return
        suffix = Path(filename).suffix or ".bin"
        try:
            with TemporaryDirectory() as temporary_dir:
                download_path = Path(temporary_dir) / f"download{suffix}"
                telegram_file = await attachment.get_file()  # type: ignore[attr-defined]
                await telegram_file.download_to_drive(download_path)
                response = await asyncio.to_thread(
                    self._event_handler.handle_file, event, download_path
                )
            await self._reply(message, event, response)
        except BaseException:
            self._release(event)
            raise
        self._mark_delivered(event)

    async def handle_callback(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Resolve a durable button token into its locally stored command."""
        del context
        query = update.callback_query
        if query is None or query.message is None or not query.data:
            return
        await query.answer()
        message = query.message
        event = normalize_telegram_update(update)
        if not self._is_allowed(event):
            await message.reply_text("This Steward bot is not authorized for this chat.")
            return
        if self._callbacks is None:
            await message.reply_text("This Steward action is no longer available. Send /help.")
            return
        callback = self._callbacks.resolve(query.data, event.chat_id)
        if callback is None:
            await message.reply_text("This Steward action is invalid or has expired. Send /help.")
            return
        callback_event = replace(
            event,
            id=f"telegram:callback:{query.id}",
            text=callback.command,
        )
        if not self._claim(callback_event):
            return
        try:
            response = await asyncio.to_thread(self._event_handler.handle, callback_event)
            await self._reply(message, callback_event, response)
        except BaseException:
            self._release(callback_event)
            raise
        self._mark_delivered(callback_event)

    async def _reply(self, message: object, event: IncomingEvent, response: object) -> None:
        """Render escaped HTML and locally-resolved compact follow-up buttons."""
        if isinstance(response, PresentedReply):
            self._remember_review(event, response)
        rendered_messages = self._presenter.render_many(
            response if isinstance(response, PresentedReply) else str(response)
        )
        for rendered in rendered_messages:
            if not rendered.rows or self._callbacks is None:
                await message.reply_text(rendered.text, parse_mode="HTML")  # type: ignore[attr-defined]
                continue
            buttons = [
                [
                    InlineKeyboardButton(
                        action.label,
                        callback_data=self._callbacks.create(event.chat_id, action.command).token,
                    )
                    for action in row
                ]
                for row in rendered.rows
            ]
            await message.reply_text(  # type: ignore[attr-defined]
                rendered.text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(buttons)
            )

    def _remember_review(self, event: IncomingEvent, response: PresentedReply) -> None:
        """Associate a displayed approval card with this chat, never with its text."""
        if self._review_contexts is None:
            return
        for action in response.actions:
            command, _, argument = action.command.partition(" ")
            parts = argument.split()
            if command == "/approve_action" and parts and parts[0].isdigit():
                self._review_contexts.set(event.platform, event.chat_id, "action", int(parts[0]))
                return
            if command == "/organization_accept" and parts and parts[0].isdigit():
                self._review_contexts.set(event.platform, event.chat_id, "organization", int(parts[0]))
                return
            if command == "/intake_accept" and parts and parts[0].isdigit():
                self._review_contexts.set(event.platform, event.chat_id, "intake", int(parts[0]))
                return
            if command == "/review_enrichment" and parts and parts[0].isdigit():
                self._review_contexts.set(event.platform, event.chat_id, "knowledge", int(parts[0]))
                return
            if command == "/review" and len(parts) == 2 and parts[1].isdigit():
                self._review_contexts.set(event.platform, event.chat_id, parts[0], int(parts[1]))
                return

    def _is_allowed(self, event: IncomingEvent) -> bool:
        return not self._allowed_chat_ids or event.chat_id in self._allowed_chat_ids

    def _claim(self, event: IncomingEvent) -> bool:
        return self._deliveries is None or self._deliveries.claim(event.id)

    def _mark_delivered(self, event: IncomingEvent) -> None:
        if self._deliveries is not None:
            self._deliveries.mark_delivered(event.id)

    def _release(self, event: IncomingEvent) -> None:
        if self._deliveries is not None:
            self._deliveries.release(event.id)


def _attachment_names(message: object) -> tuple[str, ...]:
    """Give captured uploads a stable, safe original-name hint."""

    document = getattr(message, "document", None)
    if document is not None and getattr(document, "file_name", None):
        return (str(document.file_name),)
    if getattr(message, "photo", None):
        return (f"telegram-photo-{message.message_id}.jpg",)
    return ()


def run_telegram_polling(
    token: str,
    event_handler: IncomingEventHandler,
    capture_handler: IncomingEventHandler,
    *,
    allowed_chat_ids: frozenset[str] = frozenset(),
    delivery_repository: TelegramUpdateDeliveryRepository | None = None,
    callback_repository: TelegramCallbackRepository | None = None,
    review_contexts: ReviewContextRepository | None = None,
    task_reminders: TaskReminderService | None = None,
) -> None:
    """Start the local Telegram process until the user stops it."""

    if not token.strip():
        raise ValueError("Telegram bot token must not be empty.")

    builder = ApplicationBuilder().token(token)
    reminder_stop: asyncio.Event | None = None
    reminder_worker: asyncio.Task | None = None

    async def start_services(application: object) -> None:
        """Set a small discovery menu without making Telegram startup depend on it."""
        nonlocal reminder_stop, reminder_worker
        try:
            await application.bot.set_my_commands(  # type: ignore[attr-defined]
                [BotCommand(command, description) for command, description in _PRIMARY_COMMANDS]
            )
        except Exception:
            _LOGGER.warning("Could not update Steward's Telegram command menu.")
        if task_reminders is not None:
            reminder_stop = asyncio.Event()
            # Own this task explicitly: an infinite Application.create_task
            # worker must not be awaited by Application.stop before we signal it.
            reminder_worker = asyncio.create_task(
                _task_reminder_loop(application, task_reminders, allowed_chat_ids, stop=reminder_stop),
                name="steward-task-reminders",
            )

    async def stop_services(application: object) -> None:
        if reminder_stop is not None:
            reminder_stop.set()
        if reminder_worker is not None:
            await reminder_worker

    builder = builder.post_init(start_services)
    builder = builder.post_stop(stop_services)
    application = builder.build()
    adapter = TelegramAdapter(
        event_handler,
        allowed_chat_ids=allowed_chat_ids,
        delivery_repository=delivery_repository,
        callback_repository=callback_repository,
        review_contexts=review_contexts,
    )
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, adapter.handle_update)
    )
    application.add_handler(CommandHandler("action_proposals", adapter.handle_update))
    application.add_handler(CommandHandler("create_workspace", adapter.handle_update))
    application.add_handler(CommandHandler("propose_reextract", adapter.handle_update))
    application.add_handler(CommandHandler("propose_rebuild_index", adapter.handle_update))
    application.add_handler(CommandHandler("propose_unregister_source", adapter.handle_update))
    application.add_handler(CommandHandler("approve_action", adapter.handle_update))
    application.add_handler(CommandHandler("reject_action", adapter.handle_update))
    application.add_handler(CommandHandler("drive_import", adapter.handle_update))
    application.add_handler(CommandHandler("drive_search", adapter.handle_update))
    application.add_handler(CommandHandler("gmail_import", adapter.handle_update))
    application.add_handler(CommandHandler("gmail_search", adapter.handle_update))
    application.add_handler(CommandHandler("help", adapter.handle_update))
    application.add_handler(CommandHandler("home", adapter.handle_update))
    application.add_handler(CommandHandler("pending", adapter.handle_update))
    application.add_handler(CommandHandler("review", adapter.handle_update))
    application.add_handler(CommandHandler("status", adapter.handle_update))
    application.add_handler(CommandHandler("inbox", adapter.handle_update))
    application.add_handler(CommandHandler("sources", adapter.handle_update))
    application.add_handler(CommandHandler("source", adapter.handle_update))
    application.add_handler(CommandHandler("source_content", adapter.handle_update))
    application.add_handler(CommandHandler("summarize_source", adapter.handle_update))
    application.add_handler(CommandHandler("ask_source", adapter.handle_update))
    application.add_handler(CommandHandler("workspaces", adapter.handle_update))
    application.add_handler(CommandHandler("workspace", adapter.handle_update))
    application.add_handler(CommandHandler("activity", adapter.handle_update))
    application.add_handler(CommandHandler("metrics", adapter.handle_update))
    application.add_handler(CommandHandler("search", adapter.handle_update))
    application.add_handler(CommandHandler("calendar", adapter.handle_update))
    application.add_handler(CommandHandler("semantic_search", adapter.handle_update))
    application.add_handler(CommandHandler("hybrid_search", adapter.handle_update))
    application.add_handler(CommandHandler("organize", adapter.handle_update))
    application.add_handler(CommandHandler("organization_proposals", adapter.handle_update))
    application.add_handler(CommandHandler("agent", adapter.handle_update))
    application.add_handler(CommandHandler("records", adapter.handle_update))
    application.add_handler(CommandHandler("tasks", adapter.handle_update))
    application.add_handler(CommandHandler("task", adapter.handle_update))
    application.add_handler(CommandHandler("complete_task", adapter.handle_update))
    application.add_handler(CommandHandler("propose_task", adapter.handle_update))
    application.add_handler(CommandHandler("research", adapter.handle_update))
    application.add_handler(CommandHandler("research_retain", adapter.handle_update))
    application.add_handler(CommandHandler("research_retain_token", adapter.handle_update))
    application.add_handler(CommandHandler("research_retain_source_token", adapter.handle_update))
    application.add_handler(CommandHandler("propose_note", adapter.handle_update))
    application.add_handler(CommandHandler("curate", adapter.handle_update))
    application.add_handler(CommandHandler("curate_synthesize", adapter.handle_update))
    application.add_handler(CommandHandler("curate_edit", adapter.handle_update))
    application.add_handler(CommandHandler("propose_link_source", adapter.handle_update))
    application.add_handler(CommandHandler("integrations", adapter.handle_update))
    application.add_handler(CommandHandler("propose_travel_record", adapter.handle_update))
    application.add_handler(CommandHandler("propose_receipt_record", adapter.handle_update))
    application.add_handler(CommandHandler("propose_warranty_record", adapter.handle_update))
    application.add_handler(CommandHandler("travel_references", adapter.handle_update))
    application.add_handler(CommandHandler("propose_travel_reference", adapter.handle_update))
    application.add_handler(CommandHandler("correct_travel_record", adapter.handle_update))
    application.add_handler(CommandHandler("correct_receipt_record", adapter.handle_update))
    application.add_handler(CommandHandler("correct_warranty_record", adapter.handle_update))
    application.add_handler(CommandHandler("calendar_travel", adapter.handle_update))
    application.add_handler(CommandHandler("calendar_task", adapter.handle_update))
    application.add_handler(CommandHandler("knowledge", adapter.handle_update))
    application.add_handler(CommandHandler("connect_knowledge", adapter.handle_update))
    application.add_handler(CommandHandler("knowledge_proposals", adapter.handle_update))
    application.add_handler(CommandHandler("knowledge_proposal", adapter.handle_update))
    application.add_handler(CommandHandler("propose_enrichment", adapter.handle_update))
    application.add_handler(CommandHandler("review_enrichment", adapter.handle_update))
    application.add_handler(CommandHandler("roots", adapter.handle_update))
    application.add_handler(CommandHandler("root", adapter.handle_update))
    application.add_handler(CommandHandler("privacy", adapter.handle_update))
    application.add_handler(CommandHandler("set_privacy", adapter.handle_update))
    application.add_handler(CommandHandler("deliveries", adapter.handle_update))
    application.add_handler(CommandHandler("delivery_history", adapter.handle_update))
    application.add_handler(CommandHandler("dead_letters", adapter.handle_update))
    application.add_handler(CommandHandler("recover_dead_letter", adapter.handle_update))
    application.add_handler(CommandHandler("calendar_search", adapter.handle_update))
    application.add_handler(CommandHandler("calendar_get", adapter.handle_update))
    application.add_handler(CommandHandler("organization_accept", adapter.handle_update))
    application.add_handler(CommandHandler("organization_reject", adapter.handle_update))
    application.add_handler(CommandHandler("organization_context", adapter.handle_update))
    application.add_handler(CommandHandler("organization_new_workspace", adapter.handle_update))
    application.add_handler(CommandHandler("organization_keep_inbox", adapter.handle_update))
    application.add_handler(CommandHandler("intake_accept", adapter.handle_update))
    application.add_handler(CommandHandler("intake_discard", adapter.handle_update))
    application.add_handler(CommandHandler("intake_context", adapter.handle_update))
    application.add_handler(CommandHandler("intake_analysis", adapter.handle_update))
    capture_adapter = TelegramAdapter(
        capture_handler,
        allowed_chat_ids=allowed_chat_ids,
        delivery_repository=delivery_repository,
        callback_repository=callback_repository,
        review_contexts=review_contexts,
    )
    application.add_handler(CommandHandler("save", capture_adapter.handle_update))
    # Keep this after every known command (especially /save). Unknown slash
    # commands should receive Steward's safe help/clarification rather than
    # being silently discarded by Telegram's command filter.
    application.add_handler(MessageHandler(filters.COMMAND, adapter.handle_update))
    application.add_handler(MessageHandler(filters.Document.ALL, capture_adapter.handle_document))
    application.add_handler(MessageHandler(filters.PHOTO, capture_adapter.handle_photo))
    application.add_handler(CallbackQueryHandler(adapter.handle_callback))
    application.run_polling()


async def deliver_due_task_reminders(
    bot: object,
    reminders: TaskReminderService,
    allowed_chat_ids: frozenset[str] = frozenset(),
) -> int:
    """Deliver claimed reminders, releasing a claim if Telegram does not accept it."""
    delivered = 0
    for reminder in await asyncio.to_thread(reminders.claim_due):
        task_id = reminder.task.id or 0
        if allowed_chat_ids and reminder.chat_id not in allowed_chat_ids:
            await asyncio.to_thread(reminders.release, task_id)
            continue
        text = (
            f"Reminder: Task {task_id}: {reminder.task.title}\n"
            f"Scheduled for: {reminder.remind_at.isoformat()}"
        )
        try:
            await bot.send_message(chat_id=reminder.chat_id, text=text)  # type: ignore[attr-defined]
        except Exception:
            await asyncio.to_thread(reminders.release, task_id)
            continue
        await asyncio.to_thread(reminders.acknowledge, task_id)
        delivered += 1
    return delivered


async def _task_reminder_loop(
    application: object,
    reminders: TaskReminderService,
    allowed_chat_ids: frozenset[str],
    *,
    stop: asyncio.Event,
) -> None:
    """Poll due reminder records independently of Telegram update delivery."""
    while not stop.is_set():
        try:
            await deliver_due_task_reminders(application.bot, reminders, allowed_chat_ids)  # type: ignore[attr-defined]
        except Exception:
            _LOGGER.warning("Reminder delivery cycle failed; retrying on the next cycle.")
        try:
            await asyncio.wait_for(stop.wait(), timeout=60)
        except TimeoutError:
            pass
