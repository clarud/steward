"""Heading-based structural extraction for Markdown sources."""

from __future__ import annotations

import re

from steward.extraction.models import ExtractionResult, SourceFragment
from steward.sources.models import Source

HEADING_PATTERN = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")


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
            text = "\n".join(lines[start_line - 1 : next_start_line - 1]).strip()
            if not text:
                continue

            fragments.append(
                SourceFragment(
                    id=None,
                    source_id=source_id,
                    heading=heading,
                    ordinal=len(fragments),
                    text=text,
                    location=f"lines {start_line}-{next_start_line - 1}",
                )
            )

        return ExtractionResult(source_id=source_id, fragments=tuple(fragments))

