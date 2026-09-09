"""Human-editable, deterministic evaluation for local lexical retrieval."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from steward.retrieval import LexicalSearchService


@dataclass(frozen=True, slots=True)
class RetrievalCase:
    """One query and the source fragment a person expects it to find."""

    query: str
    expected_source: str
    expected_heading: str


@dataclass(frozen=True, slots=True)
class RetrievalMiss:
    """A transparent record of one expected fragment absent from the top five."""

    query: str
    expected_source: str
    expected_heading: str


@dataclass(frozen=True, slots=True)
class RetrievalEvaluation:
    case_count: int
    recall_at_5: float
    mean_reciprocal_rank: float
    misses: tuple[RetrievalMiss, ...]


def load_retrieval_cases(path: Path) -> tuple[RetrievalCase, ...]:
    """Load a small YAML fixture without embedding personal vault contents in code."""

    document: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8"))
    cases = document.get("cases") if isinstance(document, dict) else None
    if not isinstance(cases, list) or not cases:
        raise ValueError("Retrieval cases must contain a non-empty 'cases' list.")
    parsed = []
    for item in cases:
        if not isinstance(item, dict) or not isinstance(item.get("expected"), dict):
            raise ValueError("Each retrieval case requires query and expected fields.")
        query = item.get("query")
        expected_source = item["expected"].get("source")
        expected_heading = item["expected"].get("heading")
        if not all(isinstance(value, str) and value.strip() for value in (query, expected_source, expected_heading)):
            raise ValueError("Retrieval case query, expected source, and expected heading must be non-empty text.")
        parsed.append(RetrievalCase(query, expected_source, expected_heading))
    return tuple(parsed)


def evaluate_lexical_retrieval(
    service: LexicalSearchService,
    cases: tuple[RetrievalCase, ...],
    vault_root: Path,
) -> RetrievalEvaluation:
    """Measure Recall@5 and MRR using paths relative to the evaluated vault."""

    if not cases:
        raise ValueError("At least one retrieval case is required.")
    root = vault_root.resolve()
    reciprocal_ranks: list[float] = []
    misses: list[RetrievalMiss] = []
    for case in cases:
        rank: int | None = None
        for position, hit in enumerate(service.search(case.query, limit=5), start=1):
            try:
                source_name = hit.source.path.relative_to(root).as_posix()
            except ValueError:
                continue
            if source_name == case.expected_source and hit.fragment.heading == case.expected_heading:
                rank = position
                break
        if rank is None:
            reciprocal_ranks.append(0)
            misses.append(RetrievalMiss(case.query, case.expected_source, case.expected_heading))
        else:
            reciprocal_ranks.append(1 / rank)
    return RetrievalEvaluation(
        case_count=len(cases),
        recall_at_5=(len(cases) - len(misses)) / len(cases),
        mean_reciprocal_rank=sum(reciprocal_ranks) / len(cases),
        misses=tuple(misses),
    )
