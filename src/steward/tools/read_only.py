"""Read-only tools exposed to a model during Phase 21."""

from __future__ import annotations

import json
from pathlib import Path

from langchain_core.tools import BaseTool, tool

from steward.activity import ActivityService
from steward.extraction import SourceFragmentRepository
from steward.knowledge import KnowledgeService
from steward.records import RecordService
from steward.retrieval import LexicalSearchService
from steward.sources import SourceRepository
from steward.workspaces import WorkspaceRepository


class ReadOnlyToolService:
    """Adapt ordinary domain services to JSON-safe, read-only tool results."""

    def __init__(
        self,
        source_repository: SourceRepository,
        fragment_repository: SourceFragmentRepository,
        lexical_search: LexicalSearchService,
        knowledge_service: KnowledgeService,
        record_service: RecordService,
        workspace_repository: WorkspaceRepository,
        activity_service: ActivityService,
    ) -> None:
        self._sources = source_repository
        self._fragments = fragment_repository
        self._lexical = lexical_search
        self._knowledge = knowledge_service
        self._records = record_service
        self._workspaces = workspace_repository
        self._activity = activity_service

    def search_sources(self, query: str, limit: int = 5) -> str:
        """Find source fragments by exact terms and return their provenance."""
        return self._json(
            [
                {
                    "source_id": hit.source.id,
                    "path": str(hit.source.path),
                    "fragment_id": hit.fragment.id,
                    "heading": hit.fragment.heading,
                    "location": hit.fragment.location,
                    "text": hit.fragment.text,
                    "score": hit.score,
                }
                for hit in self._lexical.search(query, limit=self._limit(limit))
            ]
        )

    def read_source(self, source_id: int) -> str:
        """Read one registered source's metadata and extracted fragments."""
        source = self._sources.get_by_id(source_id)
        if source is None:
            return self._json({"error": f"Source {source_id} was not found."})
        return self._json(
            {
                "id": source.id,
                "path": str(source.path),
                "source_type": source.source_type.value,
                "fragments": [
                    {
                        "id": fragment.id,
                        "heading": fragment.heading,
                        "location": fragment.location,
                        "text": fragment.text,
                    }
                    for fragment in self._fragments.list_for_source(source_id)
                ],
            }
        )

    def search_knowledge(self, query: str) -> str:
        """Resolve a canonical concept or alias without creating knowledge."""
        concept = self._knowledge.find(query)
        return self._json(
            {
                "concept": None
                if concept is None
                else {"id": concept.id, "name": concept.name, "created_at": concept.created_at.isoformat()}
            }
        )

    def search_records(self, query: str, limit: int = 10) -> str:
        """Find saved travel records by flight, location, or booking reference."""
        needle = query.casefold().strip()
        records = []
        for record in self._records.list_travel_records():
            values = (record.flight_number, record.departure, record.arrival, record.booking_reference)
            if any(needle in value.casefold() for value in values if value):
                records.append(
                    {
                        "id": record.id,
                        "source_id": record.source_id,
                        "flight_number": record.flight_number,
                        "departure": record.departure,
                        "arrival": record.arrival,
                        "departure_time": record.departure_time.isoformat() if record.departure_time else None,
                        "arrival_time": record.arrival_time.isoformat() if record.arrival_time else None,
                        "booking_reference": record.booking_reference,
                    }
                )
        return self._json(records[: self._limit(limit)])

    def search_workspaces(self, query: str) -> str:
        """Find active workspace context by name without changing it."""
        needle = query.casefold().strip()
        return self._json(
            [
                {"id": workspace.id, "name": workspace.name, "status": workspace.status}
                for workspace in self._workspaces.list_all()
                if needle in workspace.name.casefold()
            ]
        )

    def search_activity(self, query: str = "", limit: int = 10) -> str:
        """Find recent audit events by type, details, or object ID."""
        needle = query.casefold().strip()
        events = self._activity.list_recent(limit=self._limit(limit))
        return self._json(
            [
                {
                    "id": event.id,
                    "event_type": event.event_type.value,
                    "object_id": event.object_id,
                    "details": event.details,
                    "occurred_at": event.occurred_at.isoformat(),
                }
                for event in events
                if not needle
                or needle in event.event_type.value
                or needle in event.details.casefold()
                or needle in (event.object_id or "").casefold()
            ]
        )

    @staticmethod
    def _limit(value: int) -> int:
        if not 1 <= value <= 20:
            raise ValueError("Tool limit must be between 1 and 20.")
        return value

    @staticmethod
    def _json(value: object) -> str:
        return json.dumps(value, ensure_ascii=False)


def build_read_only_tools(service: ReadOnlyToolService) -> list[BaseTool]:
    """Return LangChain tool schemas over Steward's safe read APIs only."""

    @tool
    def search_sources(query: str, limit: int = 5) -> str:
        """Search locally indexed source fragments by terms. Use before reading a source."""
        return service.search_sources(query, limit)

    @tool
    def read_source(source_id: int) -> str:
        """Read one source and its extracted fragments by Steward source ID."""
        return service.read_source(source_id)

    @tool
    def search_knowledge(query: str) -> str:
        """Find a canonical Steward concept by name or alias."""
        return service.search_knowledge(query)

    @tool
    def search_records(query: str, limit: int = 10) -> str:
        """Find locally saved travel records by route, flight, or booking reference."""
        return service.search_records(query, limit)

    @tool
    def search_workspaces(query: str) -> str:
        """Find known Steward workspaces by name."""
        return service.search_workspaces(query)

    @tool
    def search_activity(query: str = "", limit: int = 10) -> str:
        """Search Steward's recent audit history without changing anything."""
        return service.search_activity(query, limit)

    return [search_sources, read_source, search_knowledge, search_records, search_workspaces, search_activity]
