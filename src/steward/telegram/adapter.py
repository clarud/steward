"""Telegram long-polling adapter for questions and explicit file capture."""

from __future__ import annotations

import asyncio
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Protocol

from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes, MessageHandler, filters

from steward.events import IncomingEvent
from steward.telegram.delivery import TelegramUpdateDeliveryRepository


class IncomingEventHandler(Protocol):
    """Application boundary invoked after a platform update is normalized."""

    def handle(self, event: IncomingEvent) -> str: ...


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
    ) -> None:
        self._event_handler = event_handler
        self._allowed_chat_ids = allowed_chat_ids
        self._deliveries = delivery_repository

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
            await message.reply_text(response)
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
        if not (message.caption or "").strip().startswith("/save"):
            await message.reply_text("Add /save as the attachment caption to preserve it.")
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
            await message.reply_text(response)
        except BaseException:
            self._release(event)
            raise
        self._mark_delivered(event)

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
) -> None:
    """Start the local Telegram process until the user stops it."""

    if not token.strip():
        raise ValueError("Telegram bot token must not be empty.")

    application = ApplicationBuilder().token(token).build()
    adapter = TelegramAdapter(
        event_handler,
        allowed_chat_ids=allowed_chat_ids,
        delivery_repository=delivery_repository,
    )
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, adapter.handle_update)
    )
    application.add_handler(CommandHandler("action_proposals", adapter.handle_update))
    application.add_handler(CommandHandler("create_workspace", adapter.handle_update))
    application.add_handler(CommandHandler("approve_action", adapter.handle_update))
    application.add_handler(CommandHandler("reject_action", adapter.handle_update))
    application.add_handler(CommandHandler("drive_import", adapter.handle_update))
    application.add_handler(CommandHandler("gmail_import", adapter.handle_update))
    application.add_handler(CommandHandler("help", adapter.handle_update))
    application.add_handler(CommandHandler("status", adapter.handle_update))
    application.add_handler(CommandHandler("inbox", adapter.handle_update))
    application.add_handler(CommandHandler("sources", adapter.handle_update))
    application.add_handler(CommandHandler("source", adapter.handle_update))
    application.add_handler(CommandHandler("workspaces", adapter.handle_update))
    application.add_handler(CommandHandler("activity", adapter.handle_update))
    application.add_handler(CommandHandler("search", adapter.handle_update))
    capture_adapter = TelegramAdapter(
        capture_handler,
        allowed_chat_ids=allowed_chat_ids,
        delivery_repository=delivery_repository,
    )
    application.add_handler(CommandHandler("save", capture_adapter.handle_update))
    application.add_handler(MessageHandler(filters.Document.ALL, capture_adapter.handle_document))
    application.add_handler(MessageHandler(filters.PHOTO, capture_adapter.handle_photo))
    application.run_polling()
