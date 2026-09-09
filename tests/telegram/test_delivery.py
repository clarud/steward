from pathlib import Path
from datetime import UTC, datetime, timedelta

from steward.storage import initialize_database
from steward.telegram import TelegramUpdateDeliveryRepository


def test_delivery_repository_claims_once_and_releases_failures(tmp_path: Path) -> None:
    database_path = tmp_path / "steward.db"
    initialize_database(database_path)
    deliveries = TelegramUpdateDeliveryRepository(database_path)

    assert deliveries.claim("telegram:42") is True
    assert deliveries.claim("telegram:42") is False


def test_delivery_repository_reclaims_a_stale_processing_lease(tmp_path: Path) -> None:
    database_path = tmp_path / "steward.db"; initialize_database(database_path)
    deliveries = TelegramUpdateDeliveryRepository(database_path, lease_seconds=60)
    started = datetime(2026, 9, 9, tzinfo=UTC)

    assert deliveries.claim("telegram:43", now=started) is True
    assert deliveries.claim("telegram:43", now=started + timedelta(seconds=59)) is False
    assert deliveries.claim("telegram:43", now=started + timedelta(seconds=60)) is True
    deliveries.release("telegram:42")
    assert deliveries.claim("telegram:42") is True
    deliveries.mark_delivered("telegram:42")
    assert deliveries.claim("telegram:42") is False


def test_delivery_repository_lists_metadata_without_message_content(tmp_path: Path) -> None:
    database_path = tmp_path / "steward.db"; initialize_database(database_path)
    deliveries = TelegramUpdateDeliveryRepository(database_path)
    started = datetime(2026, 9, 9, tzinfo=UTC)
    assert deliveries.claim("telegram:99", now=started)

    records = deliveries.list_recent()

    assert len(records) == 1
    assert records[0].update_id == "telegram:99"
    assert records[0].status == "processing"
    assert records[0].claimed_at == started
    assert records[0].delivered_at is None


def test_delivery_history_retains_a_released_attempt(tmp_path: Path) -> None:
    database_path = tmp_path / "steward.db"; initialize_database(database_path)
    deliveries = TelegramUpdateDeliveryRepository(database_path)

    assert deliveries.claim("telegram:100")
    deliveries.release("telegram:100")

    import sqlite3
    with sqlite3.connect(database_path) as connection:
        history = connection.execute(
            "SELECT update_id, event_type FROM telegram_delivery_history ORDER BY id"
        ).fetchall()
    assert history == [("telegram:100", "claimed"), ("telegram:100", "released")]
