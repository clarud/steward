from datetime import UTC, datetime
from pathlib import Path

from steward.capture import InboxCaptureService
from steward.events import IncomingEvent
from steward.sources import SourceRepository
from steward.storage import initialize_database


def test_capture_text_preserves_and_registers_an_inbox_source(tmp_path: Path) -> None:
    database_path = tmp_path / ".steward" / "steward.db"
    initialize_database(database_path)
    event = IncomingEvent(
        id="telegram:42", platform="telegram", chat_id="100", message_id="7",
        reply_to_id=None, timestamp=datetime(2026, 9, 8, tzinfo=UTC), text="Save this note.",
    )
    service = InboxCaptureService(tmp_path / "vault" / "inbox", SourceRepository(database_path))

    result = service.capture_text(event)

    assert result.duplicate is False
    assert result.source.path.read_text(encoding="utf-8") == "Save this note."
    assert result.source.path.parent.name == "inbox"


def test_capture_text_is_idempotent_for_the_same_message(tmp_path: Path) -> None:
    database_path = tmp_path / "steward.db"
    initialize_database(database_path)
    event = IncomingEvent("telegram:42", "telegram", "100", "7", None, datetime(2026, 9, 8, tzinfo=UTC), "Save this note.")
    service = InboxCaptureService(tmp_path / "inbox", SourceRepository(database_path))

    first = service.capture_text(event)
    second = service.capture_text(event)

    assert first.source.id == second.source.id
    assert second.duplicate is True


def test_capture_file_preserves_a_pdf_original_without_extraction(tmp_path: Path) -> None:
    database_path = tmp_path / "steward.db"
    initialize_database(database_path)
    original = tmp_path / "itinerary.pdf"
    original.write_bytes(b"%PDF-example")
    event = IncomingEvent("telegram:43", "telegram", "100", "8", None, datetime(2026, 9, 8, tzinfo=UTC), None)
    service = InboxCaptureService(tmp_path / "inbox", SourceRepository(database_path))

    result = service.capture_file(event, original)

    assert result.source.source_type.value == "pdf"
    assert result.source.path.read_bytes() == b"%PDF-example"
