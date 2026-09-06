"""Small, explicit evaluation helpers for lexical retrieval."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from steward.retrieval import LexicalSearchService


@dataclass(frozen=True, slots=True)
class RetrievalCase:
    query: str
    expected_source: str
    expected_heading: str


@dataclass(frozen=True, slots=True)
class RetrievalEvaluation:
    case_count: int
    recall_at_5: float
    mean_reciprocal_rank: float


def load_retrieval_cases(path: Path) -> tuple[RetrievalCase, ...]:
    """Load the deliberately human-editable retrieval case fixture."""
    document: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8"))
    return tuple(
        RetrievalCase(
            query=item["query"],
            expected_source=item["expected"]["source"],
            expected_heading=item["expected"]["heading"],
        )
        for item in document["cases"]
    )


def evaluate_lexical_retrieval(
    service: LexicalSearchService,
    cases: tuple[RetrievalCase, ...],
    fixture_root: Path,
) -> RetrievalEvaluation:
    """Measure whether each expected fragment appears in the lexical top five."""
    reciprocal_ranks: list[float] = []
    found_count = 0

    for case in cases:
        rank: int | None = None
        for position, hit in enumerate(service.search(case.query, limit=5), start=1):
            source_name = hit.source.path.relative_to(fixture_root).as_posix()
            if (
                source_name == case.expected_source
                and hit.fragment.heading == case.expected_heading
            ):
                rank = position
                break

        if rank is not None:
            found_count += 1
            reciprocal_ranks.append(1 / rank)
        else:
            reciprocal_ranks.append(0)

    return RetrievalEvaluation(
        case_count=len(cases),
        recall_at_5=found_count / len(cases),
        mean_reciprocal_rank=sum(reciprocal_ranks) / len(cases),
    )
