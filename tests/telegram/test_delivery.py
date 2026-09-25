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














