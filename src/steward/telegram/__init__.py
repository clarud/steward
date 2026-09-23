"""Telegram transport adapter for Steward."""

from steward.events import IncomingEvent
from steward.telegram.adapter import TelegramAdapter, normalize_telegram_update, run_telegram_polling
from steward.telegram.callbacks import TelegramCallback, TelegramCallbackRepository
from steward.telegram.delivery import TelegramUpdateDeliveryRepository

__all__ = [
    "IncomingEvent",
    "TelegramAdapter",
    "TelegramCallback",
    "TelegramCallbackRepository",
    "TelegramUpdateDeliveryRepository",
    "normalize_telegram_update",
    "run_telegram_polling",
]
