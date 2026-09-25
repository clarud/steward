"""Heading-based structural extraction for Markdown sources."""

from __future__ import annotations

import re

from steward.extraction.models import ExtractionResult, SourceFragment
from steward.sources.models import Source

HEADING_PATTERN = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
# Long sections (logs, notes without headings) are split at line breaks, because
# search ranks and embeds one huge section poorly and shows it without a precise location.
MAX_SECTION_CHARACTERS = 3000


class MarkdownExtractor:
    """Split Markdown into heading-delimited fragments with line provenance."""

    def extract(self, source: Source) -> ExtractionResult:
        """Read one persisted Markdown Source and extract its structural fragments."""
        if source.id is None:
            raise ValueError("Only a persisted Source can be extracted.")

        return self.extract_text(
            source_id=source.id,
            markdown=source.path.read_text(encoding="utf-8"),
        )

    def extract_text(self, *, source_id: int, markdown: str) -> ExtractionResult:
        """Extract fragments from Markdown text for a known Source ID."""
        lines = markdown.splitlines()
        fragment_boundaries: list[tuple[str | None, int]] = [(None, 1)]

        for line_number, line in enumerate(lines, start=1):
            match = HEADING_PATTERN.match(line)
            if match:
                fragment_boundaries.append((match.group(2), line_number))

        fragments: list[SourceFragment] = []
        for ordinal, (heading, start_line) in enumerate(fragment_boundaries):
            next_start_line = (
                fragment_boundaries[ordinal + 1][1]
                if ordinal + 1 < len(fragment_boundaries)
                else len(lines) + 1
            )
            for first, last, text in _chunks(lines, start_line, next_start_line - 1):
                fragments.append(
                    SourceFragment(
                        id=None,
                        source_id=source_id,
                        heading=heading,
                        ordinal=len(fragments),
                        text=text,
                        location=f"lines {first}-{last}",
                    )
                )

        return ExtractionResult(source_id=source_id, fragments=tuple(fragments))



def _chunks(lines: list[str], first: int, last: int) -> list[tuple[int, int, str]]:
    """One section as (first line, last line, text) pieces of at most MAX_SECTION_CHARACTERS."""
    whole = "\n".join(lines[first - 1:last]).strip()
    if len(whole) <= MAX_SECTION_CHARACTERS:
        return [(first, last, whole)] if whole else []
    pieces: list[tuple[int, int, str]] = []
    buffer: list[str] = []
    start = first

    def flush(end: int) -> None:
        text = "\n".join(buffer).strip()
        if text:
            pieces.append((start, end, text))

    for number in range(first, last + 1):
        line = lines[number - 1]
        if len(line) > MAX_SECTION_CHARACTERS:  # one very long line: slice it, keeping its line number
            flush(number - 1)
            buffer, start = [], number
            for offset in range(0, len(line), MAX_SECTION_CHARACTERS):
                piece = line[offset:offset + MAX_SECTION_CHARACTERS].strip()
                if piece:
                    pieces.append((number, number, piece))
            start = number + 1
            continue
        if buffer and sum(len(item) + 1 for item in buffer) + len(line) > MAX_SECTION_CHARACTERS:
            flush(number - 1)
            buffer, start = [], number
        buffer.append(line)
    flush(last)
    return pieces
