"""File-level search evaluation: did the right file come back, and how high?"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True, slots=True)
class FileCase:
    """A request and the file a person expects; `expected` matches the end of the file's path."""

    query: str
    expected: str


@dataclass(frozen=True, slots=True)
class FileEvaluation:
    case_count: int
    hit_at_1: float
    hit_at_3: float
    mean_reciprocal_rank: float
    misses: tuple[tuple[str, str], ...]


def load_file_cases(path: Path) -> tuple[FileCase, ...]:
    """Read `cases: [{query: ..., file: ...}]` (or the older `expected: {source: ...}` form)."""
    document: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8"))
    cases = document.get("cases") if isinstance(document, dict) else None
    if not isinstance(cases, list) or not cases:
        raise ValueError("The case file needs a non-empty 'cases' list.")
    parsed = []
    for item in cases:
        expected = item.get("file") if isinstance(item, dict) else None
        if expected is None and isinstance(item, dict) and isinstance(item.get("expected"), dict):
            expected = item["expected"].get("source")
        query = item.get("query") if isinstance(item, dict) else None
        if not all(isinstance(value, str) and value.strip() for value in (query, expected)):
            raise ValueError("Every case needs a query and the expected file.")
        parsed.append(FileCase(query.strip(), expected.strip().replace("\\", "/")))
    return tuple(parsed)


def evaluate_files(rank: Callable[[str], Sequence[Path]], cases: tuple[FileCase, ...]) -> FileEvaluation:
    """Score a ranking function that returns files best-first for a query."""
    if not cases:
        raise ValueError("At least one case is required.")
    reciprocal: list[float] = []
    hits_1 = hits_3 = 0
    misses = []
    for case in cases:
        position = next(
            (index for index, path in enumerate(rank(case.query), start=1)
             if path.as_posix().casefold().endswith(case.expected.casefold())),
            None,
        )
        reciprocal.append(1 / position if position else 0.0)
        hits_1 += position == 1
        hits_3 += position is not None and position <= 3
        if position is None or position > 3:
            misses.append((case.query, case.expected))
    count = len(cases)
    return FileEvaluation(count, hits_1 / count, hits_3 / count, sum(reciprocal) / count, tuple(misses))
