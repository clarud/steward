from pathlib import Path
import pytest

from steward.reviews import MessageReferenceRepository, ReviewContextRepository
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


@pytest.mark.parametrize(
    ("kind", "identifier"),
    [
        ("source", 7),
        ("workspace", 8),
        ("task", 9),
        ("record:travel", 10),
        ("record:receipt", 11),
        ("record:warranty", 12),
        ("calendar", "000123"),
        ("calendar", "int:7"),
        ("calendar", "text:7"),
    ],
)
def test_message_reference_is_chat_scoped_restart_safe_and_type_preserving(
    tmp_path, kind, identifier
):
    database = tmp_path / "steward.db"; initialize_database(database)
    references = MessageReferenceRepository(database)
    references.set("telegram", "chat-1", "message-5", kind, identifier)

    restored = MessageReferenceRepository(database).get("telegram", "chat-1", "message-5")

    assert restored is not None
    assert restored.kind == kind
    assert restored.identifier == identifier
    assert type(restored.identifier) is type(identifier)
    assert references.get("telegram", "chat-2", "message-5") is None
    assert references.get("telegram", "chat-1", "other-message") is None


def test_message_reference_retention_is_bounded_per_chat(tmp_path):
    database = tmp_path / "steward.db"; initialize_database(database)
    references = MessageReferenceRepository(database, retained_per_chat=2)
    for message_id in ("1", "2", "3"):
        references.set("telegram", "chat", message_id, "source", int(message_id))
    references.set("telegram", "other-chat", "1", "source", 99)

    assert references.get("telegram", "chat", "1") is None
    assert references.get("telegram", "chat", "2").identifier == 2
    assert references.get("telegram", "chat", "3").identifier == 3
    assert references.get("telegram", "other-chat", "1").identifier == 99
