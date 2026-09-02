from dataclasses import FrozenInstanceError
from datetime import UTC, datetime
from pathlib import Path

import pytest

from steward.sources import Source, SourceStatus, SourceType


def make_source(**overrides: object) -> Source:
    values: dict[str, object] = {
        "id": 1,
        "path": Path("vault/virtual-memory.md"),
        "content_hash": "a" * 64,
        "source_type": SourceType.MARKDOWN,
        "size_bytes": 128,
        "modified_at": datetime(2026, 9, 3, 1, 0, tzinfo=UTC),
        "first_seen_at": datetime(2026, 9, 3, 1, 1, tzinfo=UTC),
        "last_seen_at": datetime(2026, 9, 3, 1, 1, tzinfo=UTC),
    }
    values.update(overrides)
    return Source(**values)  # type: ignore[arg-type]


def test_source_records_metadata_for_one_original_file() -> None:
    source = make_source()

    assert source.path == Path("vault/virtual-memory.md")
    assert source.source_type is SourceType.MARKDOWN
    assert source.status is SourceStatus.ACTIVE


def test_source_is_immutable() -> None:
    source = make_source()

    with pytest.raises(FrozenInstanceError):
        source.status = SourceStatus.MISSING  # type: ignore[misc]


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"id": 0}, "id must be positive"),
        ({"size_bytes": -1}, "size_bytes must not be negative"),
        ({"content_hash": "not-a-hash"}, "SHA-256"),
        ({"first_seen_at": datetime(2026, 9, 3, 1, 1)}, "timezone-aware"),
    ],
)
def test_source_rejects_invalid_metadata(
    overrides: dict[str, object], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        make_source(**overrides)

