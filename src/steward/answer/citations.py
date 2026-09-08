"""Deterministic verification of model citation keys."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

from steward.answer.models import AnswerCitation


_CITATION_PATTERN = re.compile(r"\[([A-Za-z]\d+)\]")


@dataclass(frozen=True, slots=True)
class CitationVerification:
    """Whether a model's inline evidence keys match the supplied context."""

    cited_keys: tuple[str, ...]
    valid_keys: tuple[str, ...]
    invalid_keys: tuple[str, ...]

    @property
    def is_verified(self) -> bool:
        """A grounded answer needs at least one citation and no unknown keys."""

        return bool(self.valid_keys) and not self.invalid_keys


def verify_citations(
    text: str, available_citations: Sequence[AnswerCitation]
) -> CitationVerification:
    """Compare every inline [F#] reference against supplied evidence keys."""

    cited_keys = tuple(dict.fromkeys(_CITATION_PATTERN.findall(text)))
    available_keys = {citation.key for citation in available_citations}
    valid_keys = tuple(key for key in cited_keys if key in available_keys)
    invalid_keys = tuple(key for key in cited_keys if key not in available_keys)
    return CitationVerification(cited_keys, valid_keys, invalid_keys)
