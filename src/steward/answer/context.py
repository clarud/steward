"""Construction of explicit, provenance-preserving model context."""

from __future__ import annotations

from collections.abc import Sequence

from steward.answer.models import AnswerCitation, AnswerContext
from steward.retrieval import HybridSearchHit


class ContextBuilder:
    """Format retrieved evidence into the only source text an LLM may receive."""

    def __init__(self, *, max_characters: int = 12_000) -> None:
        if max_characters <= 0:
            raise ValueError("max_characters must be positive.")
        self._max_characters = max_characters

    def build(self, question: str, hits: Sequence[HybridSearchHit]) -> AnswerContext:
        """Build a labelled prompt and its matching citation metadata."""
        citations: list[AnswerCitation] = []
        sections: list[str] = [f"Question:\n{question.strip()}\n\nEvidence:"]
        for number, hit in enumerate(hits, start=1):
            if hit.fragment.id is None:
                raise RuntimeError("Retrieved fragments must be persisted before answering.")
            key = f"F{number}"
            citation = AnswerCitation(
                key=key,
                fragment_id=hit.fragment.id,
                source_path=hit.source.path,
                heading=hit.fragment.heading,
                location=hit.fragment.location,
            )
            heading = hit.fragment.heading or "Preamble"
            prefix = "\n".join(
                (
                    f"[{key}]",
                    f"Source: {hit.source.path}",
                    f"Location: {heading}, {hit.fragment.location}",
                    "Excerpt:",
                )
            )
            used_characters = len("\n\n".join(sections))
            available_characters = self._max_characters - used_characters - len(prefix) - 2
            if available_characters < 16:
                break
            excerpt = hit.fragment.text
            if len(excerpt) > available_characters:
                excerpt = f"{excerpt[: available_characters - 16]}\n[truncated]"
            sections.append(f"{prefix}\n{excerpt}")
            citations.append(citation)
        return AnswerContext(prompt="\n\n".join(sections), citations=tuple(citations))
