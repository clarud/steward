from pathlib import Path

from steward.storage import initialize_database
from steward.telegram import TelegramUpdateDeliveryRepository


def test_delivery_repository_claims_once_and_releases_failures(tmp_path: Path) -> None:
    database_path = tmp_path / "steward.db"
    initialize_database(database_path)
    deliveries = TelegramUpdateDeliveryRepository(database_path)

    assert deliveries.claim("telegram:42") is True
    assert deliveries.claim("telegram:42") is False
    deliveries.release("telegram:42")
    assert deliveries.claim("telegram:42") is True
    deliveries.mark_delivered("telegram:42")
    assert deliveries.claim("telegram:42") is False
