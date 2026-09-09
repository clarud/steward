from datetime import UTC, datetime, timedelta

from steward.storage import initialize_database
from steward.telegram import TelegramCallbackRepository


def test_callback_is_resolved_only_by_its_original_chat(tmp_path) -> None:
    database_path = tmp_path / "steward.db"
    initialize_database(database_path)
    callbacks = TelegramCallbackRepository(database_path)
    created = callbacks.create("chat-a", "/inbox 2")

    assert callbacks.resolve(created.token, "chat-a") == created
    assert callbacks.resolve(created.token, "chat-b") is None
    assert callbacks.resolve(created.token, "chat-a") == created


def test_callback_expires_and_cannot_be_reused_after_restart(tmp_path) -> None:
    database_path = tmp_path / "steward.db"
    initialize_database(database_path)
    now = datetime(2026, 9, 9, tzinfo=UTC)
    created = TelegramCallbackRepository(database_path, ttl_seconds=10).create(
        "chat-a", "/sources 2", now=now
    )

    restarted = TelegramCallbackRepository(database_path, ttl_seconds=10)

    assert restarted.resolve(created.token, "chat-a", now=now + timedelta(seconds=11)) is None
    assert restarted.resolve(created.token, "chat-a", now=now + timedelta(seconds=12)) is None
