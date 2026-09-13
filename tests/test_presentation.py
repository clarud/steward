from datetime import UTC, datetime

from steward.presentation import calendar_time_label, timestamp_label


def test_calendar_dates_preserve_offsets_and_exclusive_all_day_end() -> None:
    assert calendar_time_label("2026-09-11T18:30:00+08:00", "2026-09-11T19:30:00+08:00") == (
        "11 Sep 2026 · 6:30 pm–7:30 pm (UTC+08:00)"
    )
    assert calendar_time_label("2026-09-11", "2026-09-12") == "11 Sep 2026 · All day"
    assert calendar_time_label("2026-09-11", "2026-09-14") == "11 Sep 2026 – 13 Sep 2026 · All day"
    travel = calendar_time_label("2026-09-11T23:00:00+08:00", "2026-09-12T06:00:00+09:00")
    assert "11 Sep 2026" in travel and "12 Sep 2026" in travel
    assert "UTC+08:00" in travel and "UTC+09:00" in travel


def test_timestamp_label_preserves_the_original_offset() -> None:
    assert timestamp_label(datetime(2026, 9, 11, 18, 30, tzinfo=UTC)) == "11 Sep 2026 · 6:30 pm (UTC+00:00)"
