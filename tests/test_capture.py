from datetime import UTC, datetime
from pathlib import Path

from steward.capture import InboxCaptureService
from steward.activity import ActivityService, ActivityType
from steward.extraction import SourceFragmentRepository
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


def test_capture_text_runs_the_full_inbox_to_fragment_and_activity_flow(tmp_path: Path) -> None:
    database_path = tmp_path / "steward.db"
    initialize_database(database_path)
    event = IncomingEvent("telegram:44", "telegram", "100", "9", None, datetime(2026, 9, 8, tzinfo=UTC), "# TLB\nCaches translations.")
    fragments = SourceFragmentRepository(database_path)
    activity = ActivityService(database_path)
    service = InboxCaptureService(tmp_path / "vault" / "inbox", SourceRepository(database_path), fragments, activity)

    captured = service.capture_text(event)

    stored = fragments.list_for_source(captured.source.id or 0)
    assert captured.source.path.read_text(encoding="utf-8") == event.text
    assert [fragment.heading for fragment in stored] == ["TLB"]
    assert [fragment.text for fragment in stored] == ["# TLB\nCaches translations."]
    assert activity.list_recent()[0].event_type is ActivityType.SOURCE_CAPTURED


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


def test_capture_file_extracts_html_into_inbox_fragments(tmp_path: Path) -> None:
    database_path = tmp_path / "steward.db"
    initialize_database(database_path)
    original = tmp_path / "notes.html"
    original.write_text("<h1>TLB</h1><p>Caches translations.</p>", encoding="utf-8")
    event = IncomingEvent("telegram:45", "telegram", "100", "10", None, datetime(2026, 9, 8, tzinfo=UTC), None)
    fragments = SourceFragmentRepository(database_path)
    service = InboxCaptureService(tmp_path / "inbox", SourceRepository(database_path), fragments)

    result = service.capture_file(event, original)

    assert result.source.source_type.value == "html"
    assert [(fragment.heading, fragment.text) for fragment in fragments.list_for_source(result.source.id or 0)] == [
        ("TLB", "Caches translations.")
    ]


def test_capture_image_keeps_original_when_optional_ocr_is_unavailable(tmp_path: Path) -> None:
    database_path = tmp_path / "steward.db"
    initialize_database(database_path)
    original = tmp_path / "receipt.png"
    original.write_bytes(b"png")
    event = IncomingEvent("telegram:46", "telegram", "100", "11", None, datetime(2026, 9, 8, tzinfo=UTC), None)
    fragments = SourceFragmentRepository(database_path)
    service = InboxCaptureService(tmp_path / "inbox", SourceRepository(database_path), fragments)

    result = service.capture_file(event, original)

    assert result.source.source_type.value == "image"
    assert result.source.path.read_bytes() == b"png"
    assert fragments.list_for_source(result.source.id or 0) == ()


def test_capture_file_retains_a_safe_version_of_the_original_name(tmp_path: Path) -> None:
    database_path = tmp_path / "steward.db"
    initialize_database(database_path)
    original = tmp_path / "original.md"
    original.write_text("# Note", encoding="utf-8")
    event = IncomingEvent(
        "telegram:44",
        "telegram",
        "100",
        "9",
        None,
        datetime(2026, 9, 8, tzinfo=UTC),
        None,
        attachments=("Steward notes!.md",),
    )

    result = InboxCaptureService(tmp_path / "inbox", SourceRepository(database_path)).capture_file(event, original)

    assert result.source.path.name == "Steward notes!.md"
    assert result.source.path.parent.name == "inbox"


def _event(message_id: str, text: str | None = None, attachment: str | None = None) -> IncomingEvent:
    return IncomingEvent(
        f"telegram:{message_id}", "telegram", "100", message_id, None, datetime(2026, 9, 8, 10, tzinfo=UTC),
        text, attachments=(attachment,) if attachment else (),
    )


def test_readable_names_avoid_clashes_and_unsafe_characters(tmp_path: Path) -> None:
    database_path = tmp_path / "steward.db"; initialize_database(database_path)
    service = InboxCaptureService(tmp_path / "inbox", SourceRepository(database_path))
    original = tmp_path / "download.pdf"; original.write_bytes(b"pdf")

    first = service.capture_file(_event("1", attachment="tut05.pdf"), original)
    second = service.capture_file(_event("2", attachment="tut05.pdf"), original)
    unsafe = service.capture_file(_event("3", attachment='week 5: "AVX"?.PDF'), original)
    note = service.capture_text(_event("4", "Compare OpenMP static/dynamic scheduling before the Friday tutorial please"))

    assert first.source.path.name == "tut05.pdf"
    assert second.source.path.name == "tut05 (2).pdf"
    assert unsafe.source.path.name == "week 5 AVX.pdf"
    assert note.source.path.name.endswith(" Compare OpenMP static dynamic scheduling before the Friday.md")
    assert note.source.path.name[:10].count("-") == 2  # starts with the capture date


def test_captures_made_before_readable_names_are_still_recognised(tmp_path: Path) -> None:
    from steward.sources import Source, SourceType
    from steward.sources.hashing import hash_file

    database_path = tmp_path / "steward.db"; initialize_database(database_path)
    sources = SourceRepository(database_path)
    service = InboxCaptureService(tmp_path / "inbox", sources)
    legacy = tmp_path / "inbox" / "telegram-100-5.md"
    legacy.parent.mkdir(); legacy.write_text("old note", encoding="utf-8")
    now = datetime(2026, 9, 1, tzinfo=UTC)
    old = sources.add(Source(None, legacy.resolve(), hash_file(legacy), SourceType.MARKDOWN, 8, now, now, now))

    again = service.capture_text(_event("5", "old note"))

    assert again.duplicate is True and again.source.id == old.id
