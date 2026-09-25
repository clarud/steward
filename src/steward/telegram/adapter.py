"""Telegram long-polling adapter for questions and explicit file capture."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Sequence
from contextlib import suppress
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Protocol, TypeVar

from telegram import BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatAction
from telegram.ext import ApplicationBuilder, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters

from steward.events import IncomingEvent
from steward.presentation import PresentedReply
from steward.telegram.presentation import TelegramPresenter
from steward.reviews import MessageReferenceRepository, ReviewContextRepository
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
T = TypeVar("T")
TYPING_REFRESH_SECONDS = 4
STARTING_NOTICE = (
    "Steward just started and is still loading its search model (usually under a minute). "
    "I'll answer as soon as it's ready."
)

_PRIMARY_COMMANDS = (
    ("find", "find a file"),
    ("ask", "answer from your files"),
    ("sources", "browse your folders"),
    ("inbox", "uploads waiting to be filed"),
    ("home", "start page"),
    ("help", "how to use Steward"),
)
# Every command the application handles. Unknown commands still reach the
# application through the catch-all handler and get a short hint.
_COMMANDS = (
    "ask", "ask_removed", "ask_source", "browse", "find", "help", "home", "inbox",
    "intake_accept", "intake_context", "intake_discard", "intake_root",
    "note", "send_source", "source", "source_content", "sources", "start",
    "summarize_source",
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
        message_references: MessageReferenceRepository | None = None,
        warming_up: Callable[[], bool] | None = None,
    ) -> None:
        self._event_handler = event_handler
        self._warming_up = warming_up
        self._told_warming: set[str] = set()
        self._allowed_chat_ids = allowed_chat_ids
        self._deliveries = delivery_repository
        self._callbacks = callback_repository
        self._review_contexts = review_contexts
        self._message_references = message_references
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
            self._restore_reply_reference(event)
            await self._notice_if_starting(message, event)
            response = await self._while_typing(message, asyncio.to_thread(self._event_handler.handle, event))
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
                "I can't download files over 20 MB through Telegram. "
                "Copy the original into the Inbox folder on your computer instead."
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
                response = await self._while_typing(message, asyncio.to_thread(
                    self._event_handler.handle_file, event, download_path
                ))
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
            await self._notice_if_starting(message, callback_event)
            response = await self._while_typing(message, asyncio.to_thread(self._event_handler.handle, callback_event))
            await self._reply(message, callback_event, response)
        except BaseException:
            self._release(callback_event)
            raise
        self._mark_delivered(callback_event)

    async def _while_typing(self, message: object, work: Awaitable[T]) -> T:
        """Show "typing…" in the chat until ``work`` finishes (Telegram clears it after 5 s)."""

        async def keep_typing() -> None:
            while True:
                with suppress(Exception):  # a cosmetic hint must never break a reply
                    await message.reply_chat_action(ChatAction.TYPING)  # type: ignore[attr-defined]
                await asyncio.sleep(TYPING_REFRESH_SECONDS)

        typing = asyncio.create_task(keep_typing())
        try:
            return await work
        finally:
            typing.cancel()
            with suppress(asyncio.CancelledError):
                await typing

    async def _notice_if_starting(self, message: object, event: IncomingEvent) -> None:
        """Tell the owner, once, that a search will wait for the model that is still loading."""
        text = (event.text or "").strip().casefold()
        if (self._warming_up is None or not self._warming_up() or event.chat_id in self._told_warming
                or not text.startswith(("/find", "/ask"))):
            return
        self._told_warming.add(event.chat_id)
        with suppress(Exception):
            await message.reply_text(STARTING_NOTICE)  # type: ignore[attr-defined]

    async def _reply(self, message: object, event: IncomingEvent, response: object) -> None:
        """Render escaped HTML and locally-resolved compact follow-up buttons."""
        if isinstance(response, PresentedReply):
            self._remember_review(event, response)
            if response.document is not None:
                sent_document = await message.reply_document(document=response.document.content, filename=response.document.filename)  # type: ignore[attr-defined]
                self._remember_message_reference(event, response, sent_document)
        rendered_messages = self._presenter.render_many(
            response if isinstance(response, PresentedReply) else str(response)
        )
        for rendered in rendered_messages:
            if not rendered.rows or self._callbacks is None:
                sent = await message.reply_text(rendered.text, parse_mode="HTML")  # type: ignore[attr-defined]
                if isinstance(response, PresentedReply):
                    self._remember_message_reference(event, response, sent)
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
            sent = await message.reply_text(  # type: ignore[attr-defined]
                rendered.text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(buttons)
            )
            if isinstance(response, PresentedReply):
                self._remember_message_reference(event, response, sent)

    def _restore_reply_reference(self, event: IncomingEvent) -> None:
        if self._message_references is None or self._review_contexts is None or event.reply_to_id is None:
            return
        try:
            reference = self._message_references.get(event.platform, event.chat_id, event.reply_to_id)
            if reference is not None:
                self._review_contexts.set(event.platform, event.chat_id, reference.kind, reference.identifier)
        except Exception:
            _LOGGER.warning("Could not restore Steward's referenced Telegram card.")

    def _remember_message_reference(self, event: IncomingEvent, response: PresentedReply, sent: object) -> None:
        if self._message_references is None:
            return
        # A card that can approve/reject a proposal represents that review,
        # even when it also links to a source. This makes a reply such as
        # "yes" resolve to the exact displayed proposal rather than to a
        # secondary object or the newest review in the chat.
        reference = self._review_reference(response) or response.reference
        if reference is None:
            return
        message_id = getattr(sent, "message_id", None)
        if message_id is None:
            return
        try:
            kind, identifier = reference
            self._message_references.set(event.platform, event.chat_id, str(message_id), kind, identifier)
        except Exception:
            # Telegram already accepted the response. Retrying this update would
            # duplicate the visible message without repairing the reference.
            _LOGGER.warning("Could not persist Steward's outbound message reference.")

    def _remember_review(self, event: IncomingEvent, response: PresentedReply) -> None:
        """Associate a displayed approval card with this chat, never with its text."""
        if self._review_contexts is None:
            return
        reference = self._review_reference(response)
        if reference is not None:
            self._review_contexts.set(event.platform, event.chat_id, *reference)

    @staticmethod
    def _review_reference(response: PresentedReply) -> tuple[str, int] | None:
        """Return the exact proposal represented by an actionable review card.

        Navigation buttons such as ``/review action 7`` only open an item and
        therefore do not select the first item on a list as an active review.
        A card becomes confirmable only when it exposes that proposal's own
        accept/reject command.
        """

        review_commands = {
            "/intake_accept": "intake",
            "/intake_discard": "intake",
        }
        for action in response.actions:
            command, _, argument = action.command.partition(" ")
            parts = argument.split()
            kind = review_commands.get(command)
            if kind is not None and parts and parts[0].isdigit():
                return kind, int(parts[0])
        return None

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
    event_handler: IncomingFileEventHandler,
    *,
    allowed_chat_ids: frozenset[str] = frozenset(),
    delivery_repository: TelegramUpdateDeliveryRepository | None = None,
    callback_repository: TelegramCallbackRepository | None = None,
    review_contexts: ReviewContextRepository | None = None,
    message_references: MessageReferenceRepository | None = None,
    periodic: Callable[[], Sequence[tuple[str, str]]] | None = None,
    period_seconds: float = 15 * 60,
    first_run_delay_seconds: float = 60,
    warming_up: Callable[[], bool] | None = None,
) -> None:
    """Start the local Telegram process until the user stops it.

    ``periodic`` runs in a worker thread ``first_run_delay_seconds`` after
    start-up (so it doesn't compete with the first request) and then every
    ``period_seconds`` (the background rescan); it returns ``(chat_id, text)``
    notices to send. ``warming_up`` reports whether meaning search is still
    loading, for the starting-up notice.
    """

    if not token.strip():
        raise ValueError("Telegram bot token must not be empty.")

    builder = ApplicationBuilder().token(token)
    background: list[asyncio.Task] = []

    async def start_services(application: object) -> None:
        """Set a small discovery menu without making Telegram startup depend on it."""
        try:
            await application.bot.set_my_commands(  # type: ignore[attr-defined]
                [BotCommand(command, description) for command, description in _PRIMARY_COMMANDS]
            )
        except Exception:
            _LOGGER.warning("Could not update Steward's Telegram command menu.")
        if periodic is not None:
            background.append(asyncio.create_task(
                run_periodically(  # type: ignore[attr-defined]
                    application.bot, periodic, period_seconds, allowed_chat_ids, first_run_delay_seconds,
                )
            ))

    async def stop_services(application: object) -> None:
        del application
        for task in background:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task

    builder = builder.post_init(start_services).post_stop(stop_services)
    application = builder.build()
    adapter = TelegramAdapter(
        event_handler,
        allowed_chat_ids=allowed_chat_ids,
        delivery_repository=delivery_repository,
        callback_repository=callback_repository,
        review_contexts=review_contexts,
        message_references=message_references,
        warming_up=warming_up,
    )
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, adapter.handle_update)
    )
    for command in _COMMANDS:
        application.add_handler(CommandHandler(command, adapter.handle_update))
    # Keep this after every known command: unknown slash commands get a hint
    # rather than being silently dropped by Telegram's command filter.
    application.add_handler(MessageHandler(filters.COMMAND, adapter.handle_update))
    application.add_handler(MessageHandler(filters.Document.ALL, adapter.handle_document))
    application.add_handler(MessageHandler(filters.PHOTO, adapter.handle_photo))
    application.add_handler(CallbackQueryHandler(adapter.handle_callback))
    application.run_polling()


async def run_periodically(
    bot: object,
    job: Callable[[], Sequence[tuple[str, str]]],
    period_seconds: float,
    allowed_chat_ids: frozenset[str] = frozenset(),
    first_run_delay_seconds: float = 0,
) -> None:
    """Run ``job`` after an initial delay and then every period; send its notices. Failures retry next period."""
    await asyncio.sleep(first_run_delay_seconds)
    while True:
        try:
            notices = await asyncio.to_thread(job)
            for chat_id, text in notices:
                if allowed_chat_ids and chat_id not in allowed_chat_ids:
                    continue
                await bot.send_message(chat_id=chat_id, text=text)  # type: ignore[attr-defined]
        except Exception as error:
            _LOGGER.warning("Background rescan failed (%s); retrying next period.", type(error).__name__)
        await asyncio.sleep(period_seconds)
