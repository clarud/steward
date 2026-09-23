"""Read-only tools exposed to a model during Phase 21."""

from __future__ import annotations

import json
import re
from pathlib import Path

from langchain_core.tools import BaseTool, tool

from steward.activity import ActivityService
from steward.extraction import SourceFragmentRepository
from steward.privacy import PrivacyService
from steward.retrieval import LexicalSearchService
from steward.extraction import InvalidSearchQueryError
from steward.sources import SourceRepository
from steward.tools.policy import ToolDefinition, ToolRisk


SOURCE_READ_ONLY_TOOL_DEFINITIONS = [
    ToolDefinition(name, False, ToolRisk.READ_ONLY, "not applicable: no mutation", None, False)
    for name in ("search_sources", "read_source", "search_activity")
]


class SourceReadOnlyToolService:
    """Minimal model boundary over sources, fragments, activity, and privacy."""

    def __init__(
        self,
        source_repository: SourceRepository,
        fragment_repository: SourceFragmentRepository,
        lexical_search: LexicalSearchService,
        activity_service: ActivityService,
        privacy_service: PrivacyService | None = None,
        model_is_local: bool = False,
    ) -> None:
        self._sources = source_repository
        self._fragments = fragment_repository
        self._lexical = lexical_search
        self._activity = activity_service
        self._privacy = privacy_service
        self._model_is_local = model_is_local

    def search_sources(self, query: str, limit: int = 5) -> str:
        try:
            hits = self._lexical.search(query, limit=self._limit(limit))
        except InvalidSearchQueryError:
            hits = ()
        filename_stem = Path(query).stem if Path(query).suffix.casefold() in {".md", ".txt", ".csv", ".eml", ".pdf"} else query
        normalized = re.sub(r"[^\w]+", " ", filename_stem.replace("_", " ")).strip()
        if not hits and normalized and normalized != query:
            hits = self._lexical.search(normalized, limit=self._limit(limit))
        elif not hits and not normalized:
            return self._json({"error": "Provide at least one searchable source term."})
        return self._json([
            {
                "source_id": hit.source.id,
                "filename": hit.source.path.name,
                "fragment_id": hit.fragment.id,
                "heading": hit.fragment.heading,
                "location": hit.fragment.location,
                "text": hit.fragment.text,
                "score": hit.score,
            }
            for hit in hits if self._permits_model(hit.source.id)
        ])

    def read_source(self, source_id: int) -> str:
        source = self._sources.get_by_id(source_id)
        if source is None:
            return self._json({"error": f"Source {source_id} was not found."})
        if not self._permits_model(source.id):
            return self._json({"error": "This source is unavailable or its privacy rule prevents use by the selected model."})
        return self._json({
            "id": source.id,
            "filename": source.path.name,
            "source_type": source.source_type.value,
            "fragments": [
                {"id": part.id, "heading": part.heading, "location": part.location, "text": part.text}
                for part in self._fragments.list_for_source(source_id)
            ],
        })

    def search_activity(self, query: str = "", limit: int = 10) -> str:
        needle = query.casefold().strip()
        return self._json([
            {
                "id": event.id,
                "event_type": event.event_type.value,
                "object_id": event.object_id,
                "details": self._safe_activity_details(event.details),
                "occurred_at": event.occurred_at.isoformat(),
            }
            for event in self._activity.list_recent(limit=self._limit(limit))
            if not needle or needle in event.event_type.value or needle in event.details.casefold()
            or needle in (event.object_id or "").casefold()
        ])

    @staticmethod
    def _limit(value: int) -> int:
        return max(1, min(value, 20))

    def _permits_model(self, source_id: int | None) -> bool:
        source = self._sources.get_by_id(source_id) if source_id is not None else None
        if source is None or source.status.value != "active":
            return False
        if self._privacy is None:
            return True
        return self._privacy.permits_local_model(source_id) if self._model_is_local else self._privacy.permits_external_model(source_id)

    @staticmethod
    def _safe_activity_details(details: str) -> str:
        """Keep audit usefulness while withholding local directory structure from a model."""
        looks_like_path = (
            details.startswith("/")
            or (len(details) > 2 and details[1] == ":" and details[2] in "\\/")
            or "/" in details
            or "\\" in details
        )
        if not looks_like_path:
            return details
        filename = details.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]
        return f"local file: {filename or '(name withheld)'}"

    @staticmethod
    def _json(value: object) -> str:
        return json.dumps(value, ensure_ascii=False)


def build_source_read_only_tools(service: SourceReadOnlyToolService) -> list[BaseTool]:
    """Return the read-only source, fragment, and activity tools."""

    @tool
    def search_sources(query: str, limit: int = 5) -> str:
        """Search locally indexed source fragments by terms."""
        return service.search_sources(query, limit)

    @tool
    def read_source(source_id: int) -> str:
        """Read one source and its extracted fragments by Steward source ID."""
        return service.read_source(source_id)

    @tool
    def search_activity(query: str = "", limit: int = 10) -> str:
        """Search source lifecycle and audit history without changing anything."""
        return service.search_activity(query, limit)

    return [search_sources, read_source, search_activity]
