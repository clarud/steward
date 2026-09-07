"""Evidence-backed, reviewable connections between knowledge concepts."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import sqlite3


@dataclass(frozen=True, slots=True)
class KnowledgeConnectionProposal:
    left_concept_id: int
    left_concept_name: str
    right_concept_id: int
    right_concept_name: str
    supporting_fragment_ids: tuple[int, ...]
    rationale: str
    where_analogy_breaks: str
    confidence: float


class KnowledgeConnector:
    """Generate candidates only where two persisted concepts share evidence."""

    def __init__(self, database_path: Path) -> None:
        self._database_path = database_path

    def propose(self) -> tuple[KnowledgeConnectionProposal, ...]:
        with sqlite3.connect(self._database_path) as connection:
            rows = connection.execute(
                """
                SELECT c1.id, c1.name, c2.id, c2.name, ce1.fragment_id
                FROM claim_evidence ce1
                JOIN claims cl1 ON cl1.id = ce1.claim_id
                JOIN claims cl2 ON cl2.id > cl1.id
                JOIN claim_evidence ce2 ON ce2.claim_id = cl2.id AND ce2.fragment_id = ce1.fragment_id
                JOIN concepts c1 ON c1.id = cl1.concept_id
                JOIN concepts c2 ON c2.id = cl2.concept_id
                WHERE c1.id != c2.id
                ORDER BY c1.id, c2.id, ce1.fragment_id
                """
            ).fetchall()
        grouped: dict[tuple[int, str, int, str], list[int]] = {}
        for left_id, left_name, right_id, right_name, fragment_id in rows:
            key = (int(left_id), str(left_name), int(right_id), str(right_name))
            grouped.setdefault(key, []).append(int(fragment_id))
        return tuple(
            KnowledgeConnectionProposal(
                left_id, left_name, right_id, right_name, tuple(fragment_ids),
                f"Both concepts are supported by {len(fragment_ids)} shared source fragment(s).",
                "Shared evidence shows co-occurrence, not that the concepts are interchangeable or causally related.",
                min(0.9, 0.4 + 0.15 * len(fragment_ids)),
            )
            for (left_id, left_name, right_id, right_name), fragment_ids in grouped.items()
        )
