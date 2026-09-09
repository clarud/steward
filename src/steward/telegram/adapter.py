"""Telegram long-polling adapter for questions and explicit file capture."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Protocol

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ApplicationBuilder, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters

from steward.events import IncomingEvent
from steward.presentation import PresentedReply
from steward.telegram.callbacks import TelegramCallbackRepository
from steward.telegram.delivery import TelegramUpdateDeliveryRepository


class IncomingEventHandler(Protocol):
    """Application boundary invoked after a platform update is normalized."""

    def handle(self, event: IncomingEvent) -> str | PresentedReply: ...


class IncomingFileEventHandler(IncomingEventHandler, Protocol):
    """Application boundary for an attachment downloaded by this adapter."""

    def handle_file(self, event: IncomingEvent, original_path: Path) -> str: ...


MAX_CLOUD_DOWNLOAD_BYTES = 20 * 1024 * 1024


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
    return IncomingEvent(
        id=f"telegram:{update.update_id}",
        platform="telegram",
        chat_id=str(message.chat_id),
        message_id=str(message.message_id),
        reply_to_id=reply_to_id,
        timestamp=message.date,
        text=message.text or message.caption,
        attachments=_attachment_names(message),
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
    ) -> None:
        self._event_handler = event_handler
        self._allowed_chat_ids = allowed_chat_ids
        self._deliveries = delivery_repository
        self._callbacks = callback_repository

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
        """Render a plain or interactive application reply without exposing commands."""
        if not isinstance(response, PresentedReply):
            await message.reply_text(str(response))  # type: ignore[attr-defined]
            return
        if not response.actions or self._callbacks is None:
            await message.reply_text(response.text)  # type: ignore[attr-defined]
            return
        buttons = [
            InlineKeyboardButton(
                action.label,
                callback_data=self._callbacks.create(event.chat_id, action.command).token,
            )
            for action in response.actions
        ]
        await message.reply_text(  # type: ignore[attr-defined]
            response.text, reply_markup=InlineKeyboardMarkup([buttons])
        )

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
) -> None:
    """Start the local Telegram process until the user stops it."""

    if not token.strip():
        raise ValueError("Telegram bot token must not be empty.")

    application = ApplicationBuilder().token(token).build()
    adapter = TelegramAdapter(
        event_handler,
        allowed_chat_ids=allowed_chat_ids,
        delivery_repository=delivery_repository,
        callback_repository=callback_repository,
    )
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, adapter.handle_update)
    )
    application.add_handler(CommandHandler("action_proposals", adapter.handle_update))
    application.add_handler(CommandHandler("create_workspace", adapter.handle_update))
    application.add_handler(CommandHandler("approve_action", adapter.handle_update))
    application.add_handler(CommandHandler("reject_action", adapter.handle_update))
    application.add_handler(CommandHandler("drive_import", adapter.handle_update))
    application.add_handler(CommandHandler("drive_search", adapter.handle_update))
    application.add_handler(CommandHandler("gmail_import", adapter.handle_update))
    application.add_handler(CommandHandler("gmail_search", adapter.handle_update))
    application.add_handler(CommandHandler("help", adapter.handle_update))
    application.add_handler(CommandHandler("status", adapter.handle_update))
    application.add_handler(CommandHandler("inbox", adapter.handle_update))
    application.add_handler(CommandHandler("sources", adapter.handle_update))
    application.add_handler(CommandHandler("source", adapter.handle_update))
    application.add_handler(CommandHandler("workspaces", adapter.handle_update))
    application.add_handler(CommandHandler("activity", adapter.handle_update))
    application.add_handler(CommandHandler("search", adapter.handle_update))
    application.add_handler(CommandHandler("organize", adapter.handle_update))
    application.add_handler(CommandHandler("agent", adapter.handle_update))
    application.add_handler(CommandHandler("records", adapter.handle_update))
    application.add_handler(CommandHandler("tasks", adapter.handle_update))
    application.add_handler(CommandHandler("complete_task", adapter.handle_update))
    application.add_handler(CommandHandler("propose_task", adapter.handle_update))
    application.add_handler(CommandHandler("research", adapter.handle_update))
    application.add_handler(CommandHandler("research_retain", adapter.handle_update))
    application.add_handler(CommandHandler("propose_note", adapter.handle_update))
    application.add_handler(CommandHandler("propose_link_source", adapter.handle_update))
    application.add_handler(CommandHandler("integrations", adapter.handle_update))
    application.add_handler(CommandHandler("propose_travel_record", adapter.handle_update))
    application.add_handler(CommandHandler("propose_receipt_record", adapter.handle_update))
    application.add_handler(CommandHandler("propose_warranty_record", adapter.handle_update))
    application.add_handler(CommandHandler("correct_travel_record", adapter.handle_update))
    application.add_handler(CommandHandler("correct_receipt_record", adapter.handle_update))
    application.add_handler(CommandHandler("correct_warranty_record", adapter.handle_update))
    application.add_handler(CommandHandler("calendar_travel", adapter.handle_update))
    application.add_handler(CommandHandler("knowledge", adapter.handle_update))
    application.add_handler(CommandHandler("knowledge_proposals", adapter.handle_update))
    application.add_handler(CommandHandler("propose_enrichment", adapter.handle_update))
    application.add_handler(CommandHandler("review_enrichment", adapter.handle_update))
    application.add_handler(CommandHandler("roots", adapter.handle_update))
    application.add_handler(CommandHandler("privacy", adapter.handle_update))
    application.add_handler(CommandHandler("set_privacy", adapter.handle_update))
    application.add_handler(CommandHandler("deliveries", adapter.handle_update))
    application.add_handler(CommandHandler("delivery_history", adapter.handle_update))
    application.add_handler(CommandHandler("dead_letters", adapter.handle_update))
    application.add_handler(CommandHandler("calendar_search", adapter.handle_update))
    application.add_handler(CommandHandler("calendar_get", adapter.handle_update))
    application.add_handler(CommandHandler("organization_accept", adapter.handle_update))
    application.add_handler(CommandHandler("organization_reject", adapter.handle_update))
    application.add_handler(CommandHandler("intake_accept", adapter.handle_update))
    application.add_handler(CommandHandler("intake_discard", adapter.handle_update))
    application.add_handler(CommandHandler("intake_context", adapter.handle_update))
    capture_adapter = TelegramAdapter(
        capture_handler,
        allowed_chat_ids=allowed_chat_ids,
        delivery_repository=delivery_repository,
        callback_repository=callback_repository,
    )
    application.add_handler(CommandHandler("save", capture_adapter.handle_update))
    application.add_handler(MessageHandler(filters.Document.ALL, capture_adapter.handle_document))
    application.add_handler(MessageHandler(filters.PHOTO, capture_adapter.handle_photo))
    application.add_handler(CallbackQueryHandler(adapter.handle_callback))
    application.run_polling()
