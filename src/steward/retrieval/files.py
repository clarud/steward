"""File-level retrieval: people look for a file, not a paragraph."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, replace

from steward.extraction import SourceFragment
from steward.sources import Source

RRF_K = 60


@dataclass(frozen=True, slots=True)
class FileCandidate:
    """One file, the section that best explains why it matched, and who found it."""

    source: Source
    fragment: SourceFragment | None
    score: float
    found_by: frozenset[str]


def group_by_file(hits: Iterable[object], found_by: str) -> list[FileCandidate]:
    """Keep each file's best-ranked section, preserving the retriever's order."""
    seen: dict[int, FileCandidate] = {}
    for hit in hits:
        source = hit.source  # type: ignore[attr-defined]
        if source.id is None or source.id in seen:
            continue
        seen[source.id] = FileCandidate(source, hit.fragment, float(hit.score), frozenset({found_by}))  # type: ignore[attr-defined]
    return list(seen.values())


def files_only(sources: Iterable[Source], found_by: str) -> list[FileCandidate]:
    return [
        FileCandidate(source, None, 0.0, frozenset({found_by}))
        for source in dict.fromkeys(sources) if source.id is not None
    ]


def fuse(
    ranked: Mapping[str, list[FileCandidate]],
    *,
    limit: int,
    boost: Callable[[Source], float] | None = None,
) -> list[FileCandidate]:
    """Reciprocal-rank fusion per file; content matches supply the explaining section."""
    merged: dict[int, FileCandidate] = {}
    for candidates in ranked.values():
        for rank, candidate in enumerate(candidates, start=1):
            source_id = candidate.source.id or 0
            contribution = 1 / (RRF_K + rank)
            existing = merged.get(source_id)
            if existing is None:
                merged[source_id] = replace(candidate, score=contribution)
                continue
            merged[source_id] = FileCandidate(
                existing.source,
                existing.fragment or candidate.fragment,
                existing.score + contribution,
                existing.found_by | candidate.found_by,
            )
    if boost is not None:
        merged = {key: replace(value, score=value.score + boost(value.source)) for key, value in merged.items()}
    return sorted(merged.values(), key=lambda item: (-item.score, item.source.path.name.casefold()))[:limit]
