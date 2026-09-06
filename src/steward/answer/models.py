"""Answer results and citations grounded in retrieved source fragments."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class AnswerCitation:
    """A human-readable pointer to one source fragment sent to the model."""

    key: str
    fragment_id: int
    source_path: Path
    heading: str | None
    location: str


@dataclass(frozen=True, slots=True)
class AnswerContext:
    """The exact retrieved evidence formatted for a model request."""

    prompt: str
    citations: tuple[AnswerCitation, ...]


@dataclass(frozen=True, slots=True)
class AnswerResult:
    """A grounded answer together with the evidence available to its model."""

    question: str
    text: str
    citations: tuple[AnswerCitation, ...]
    context: AnswerContext | None
