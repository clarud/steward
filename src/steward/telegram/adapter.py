"""Telegram long-polling adapter for text-only Steward questions."""

from __future__ import annotations

import asyncio
from typing import Protocol

from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, MessageHandler, filters

from steward.events import IncomingEvent


class IncomingEventHandler(Protocol):
    """Application boundary invoked after a platform update is normalized."""

    def handle(self, event: IncomingEvent) -> str: ...


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
        text=message.text,
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


def run_telegram_polling(token: str, event_handler: IncomingEventHandler) -> None:
    """Start the local Telegram process until the user stops it."""

    if not token.strip():
        raise ValueError("Telegram bot token must not be empty.")

    application = ApplicationBuilder().token(token).build()
    adapter = TelegramAdapter(event_handler)
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, adapter.handle_update)
    )
    application.run_polling()
