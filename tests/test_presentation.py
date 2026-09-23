from datetime import UTC, datetime

from steward.presentation import timestamp_label


def test_timestamp_label_preserves_the_original_offset() -> None:
    assert timestamp_label(datetime(2026, 9, 11, 18, 30, tzinfo=UTC)) == "11 Sep 2026 · 6:30 pm (UTC+00:00)"
