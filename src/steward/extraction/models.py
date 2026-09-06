"""Derived, provenance-preserving results of source extraction."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SourceFragment:
    """One structurally located piece of text extracted from a Source."""

    id: int | None
    source_id: int
    heading: str | None
    ordinal: int
    text: str
    location: str

    def __post_init__(self) -> None:
        if self.id is not None and self.id <= 0:
            raise ValueError("SourceFragment id must be positive.")
        if self.source_id <= 0:
            raise ValueError("SourceFragment source_id must be positive.")
        if self.ordinal < 0:
            raise ValueError("SourceFragment ordinal must not be negative.")
        if not self.text.strip():
            raise ValueError("SourceFragment text must not be empty.")
        if not self.location.strip():
            raise ValueError("SourceFragment location must not be empty.")


@dataclass(frozen=True, slots=True)
class ExtractionResult:
    """All fragments extracted from one persisted Source."""

    source_id: int
    fragments: tuple[SourceFragment, ...]

    def __post_init__(self) -> None:
        if self.source_id <= 0:
            raise ValueError("ExtractionResult source_id must be positive.")
        for expected_ordinal, fragment in enumerate(self.fragments):
            if fragment.source_id != self.source_id:
                raise ValueError("Every fragment must belong to the result's Source.")
            if fragment.ordinal != expected_ordinal:
                raise ValueError("Fragment ordinals must be consecutive from zero.")

