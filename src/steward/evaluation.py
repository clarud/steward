"""File-level search evaluation: did the right file come back, and how high?"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True, slots=True)
class FileCase:
    """A request and the file a person expects.

    ``expected`` holds one or more acceptable path endings, for files that
    exist as identical copies or as a PDF and its transcript.
    """

    query: str
    expected: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class FileEvaluation:
    case_count: int
    hit_at_1: float
    hit_at_3: float
    mean_reciprocal_rank: float
    misses: tuple[tuple[str, str], ...]


def load_file_cases(path: Path) -> tuple[FileCase, ...]:
    """Read `cases: [{query: ..., file: ...}]`; `files: [...]` lists several acceptable answers."""
    document: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8"))
    cases = document.get("cases") if isinstance(document, dict) else None
    if not isinstance(cases, list) or not cases:
        raise ValueError("The case file needs a non-empty 'cases' list.")
    parsed = []
    for number, item in enumerate(cases, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"Case {number} needs a query and the expected file.")
        expected = item.get("files", item.get("file"))
        if expected is None and isinstance(item.get("expected"), dict):
            expected = item["expected"].get("source")
        options = expected if isinstance(expected, list) else [expected]
        query = item.get("query")
        if not isinstance(query, str) or not query.strip() or not options or not all(
            isinstance(value, str) and value.strip() for value in options
        ):
            raise ValueError(f"Case {number} ({query!r}) needs a non-empty query and file.")
        parsed.append(FileCase(query.strip(), tuple(value.strip().replace("\\", "/") for value in options)))
    return tuple(parsed)


def evaluate_files(rank: Callable[[str], Sequence[Path]], cases: tuple[FileCase, ...]) -> FileEvaluation:
    """Score a ranking function that returns files best-first for a query."""
    if not cases:
        raise ValueError("At least one case is required.")
    reciprocal: list[float] = []
    hits_1 = hits_3 = 0
    misses = []
    for case in cases:
        endings = tuple(value.casefold() for value in case.expected)
        position = next(
            (index for index, path in enumerate(rank(case.query), start=1)
             if path.as_posix().casefold().endswith(endings)),
            None,
        )
        reciprocal.append(1 / position if position else 0.0)
        hits_1 += position == 1
        hits_3 += position is not None and position <= 3
        if position is None or position > 3:
            misses.append((case.query, " or ".join(case.expected)))
    count = len(cases)
    return FileEvaluation(count, hits_1 / count, hits_3 / count, sum(reciprocal) / count, tuple(misses))
