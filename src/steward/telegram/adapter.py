"""Telegram long-polling adapter for text-only Steward questions."""

from __future__ import annotations

import asyncio
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Protocol

from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes, MessageHandler, filters

from steward.events import IncomingEvent


class IncomingEventHandler(Protocol):
    """Application boundary invoked after a platform update is normalized."""

    def handle(self, event: IncomingEvent) -> str: ...


class IncomingFileEventHandler(IncomingEventHandler, Protocol):
    """Application boundary for a document downloaded by this adapter."""

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
        attachments=(message.document.file_name,)
        if message.document is not None and message.document.file_name
        else (),
    )


class TelegramAdapter:
    """Translate Telegram updates, delegate work, and send the returned text."""

    def __init__(self, event_handler: IncomingEventHandler) -> None:
        self._event_handler = event_handler

    async def handle_update(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Handle one text update without blocking Telegram's event loop."""

        del context
        event = normalize_telegram_update(update)
        response = await asyncio.to_thread(self._event_handler.handle, event)
        message = update.effective_message
        if message is None:
            raise ValueError("Telegram update does not contain a message.")
        await message.reply_text(response)

    async def handle_document(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Download an explicitly saved document, then delegate preservation."""

        del context
        message = update.effective_message
        if message is None or message.document is None:
            raise ValueError("Telegram update does not contain a document.")
        if not (message.caption or "").strip().startswith("/save"):
            await message.reply_text("Add /save as the document caption to preserve it.")
            return
        if message.document.file_size and message.document.file_size > MAX_CLOUD_DOWNLOAD_BYTES:
            await message.reply_text(
                "I cannot download files over 20 MB through the current Telegram connection. "
                "Place the original in vault/inbox instead."
            )
            return
        if not hasattr(self._event_handler, "handle_file"):
            raise TypeError("Document handling requires a file capture application.")
        event = normalize_telegram_update(update)
        suffix = Path(message.document.file_name or "attachment.bin").suffix or ".bin"
        with TemporaryDirectory() as temporary_dir:
            download_path = Path(temporary_dir) / f"download{suffix}"
            telegram_file = await message.document.get_file()
            await telegram_file.download_to_drive(download_path)
            response = await asyncio.to_thread(
                self._event_handler.handle_file, event, download_path
            )
        await message.reply_text(response)


def run_telegram_polling(
    token: str, event_handler: IncomingEventHandler, capture_handler: IncomingEventHandler
) -> None:
    """Start the local Telegram process until the user stops it."""

    if not token.strip():
        raise ValueError("Telegram bot token must not be empty.")

    application = ApplicationBuilder().token(token).build()
    adapter = TelegramAdapter(event_handler)
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, adapter.handle_update)
    )
    capture_adapter = TelegramAdapter(capture_handler)
    application.add_handler(CommandHandler("save", capture_adapter.handle_update))
    application.add_handler(MessageHandler(filters.Document.ALL, capture_adapter.handle_document))
    application.run_polling()
