"""Validation and summary helpers for non-retrieval evaluation fixtures."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


REQUIRED_FIELDS = {
    "organization": {"source", "expected_workspace", "acceptable_alternatives"},
    "records": {"source", "expected"},
    "knowledge_integration": {"existing_claim", "new_evidence", "expected_operation"},
    "tool_calling": {"request", "expected_tool", "forbidden_tools"},
    "agent_safety": {"request", "expected_risk", "requires_approval"},
}


@dataclass(frozen=True, slots=True)
class ProductEvaluationInventory:
    case_counts: dict[str, int]

    @property
    def total_cases(self) -> int:
        return sum(self.case_counts.values())


def load_product_evaluation_inventory(path: Path) -> ProductEvaluationInventory:
    """Load and reject malformed human-maintained product evaluation cases."""

    document: dict[str, list[dict[str, Any]]] = yaml.safe_load(path.read_text(encoding="utf-8"))
    if set(document) != set(REQUIRED_FIELDS):
        raise ValueError("Product evaluation file must define every Steward subsystem.")
    counts: dict[str, int] = {}
    for subsystem, fields in REQUIRED_FIELDS.items():
        cases = document[subsystem]
        if not cases:
            raise ValueError(f"{subsystem} must contain at least one evaluation case.")
        for case in cases:
            if not fields <= set(case):
                raise ValueError(f"{subsystem} evaluation case is missing required fields.")
        counts[subsystem] = len(cases)
    return ProductEvaluationInventory(counts)
