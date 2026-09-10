from pathlib import Path
import pytest

from steward.reviews import ReviewContextRepository
from steward.storage import initialize_database


def test_review_context_is_chat_scoped_and_survives_a_new_repository(tmp_path: Path) -> None:
    database = tmp_path / "steward.db"
    initialize_database(database)
    contexts = ReviewContextRepository(database)

    contexts.set("telegram", "100", "action", 7)

    restored = ReviewContextRepository(database).get("telegram", "100")
    assert restored is not None
    assert (restored.kind, restored.identifier) == ("action", 7)
    assert contexts.get("telegram", "200") is None


@pytest.mark.parametrize("external_id", ["event-opaque-1", "000123", "123456789012345678901234567890", "text:123"])
def test_review_context_preserves_an_opaque_external_identifier(tmp_path: Path, external_id: str) -> None:
    database = tmp_path / "steward.db"
    initialize_database(database)

    ReviewContextRepository(database).set("telegram", "100", "calendar", external_id)

    restored = ReviewContextRepository(database).get("telegram", "100")

    assert restored is not None
    assert (restored.kind, restored.identifier) == ("calendar", external_id)
