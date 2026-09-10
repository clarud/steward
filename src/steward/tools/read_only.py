"""Read-only tools exposed to a model during Phase 21."""

from __future__ import annotations

import json
import re
from pathlib import Path

from langchain_core.tools import BaseTool, tool

from steward.activity import ActivityService
from steward.extraction import SourceFragmentRepository
from steward.knowledge import KnowledgeService
from steward.privacy import PrivacyService
from steward.records import RecordService
from steward.retrieval import LexicalSearchService
from steward.extraction import InvalidSearchQueryError
from steward.sources import SourceRepository
from steward.workspaces import WorkspaceRepository
from steward.tools.policy import ToolDefinition, ToolRisk


READ_ONLY_TOOL_DEFINITIONS = [
    ToolDefinition(name, False, ToolRisk.READ_ONLY, "not applicable: no mutation", None, False)
    for name in (
        "search_sources", "read_source", "search_knowledge", "search_records",
        "search_workspaces", "search_activity",
    )
]


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
        privacy_service: PrivacyService | None = None,
        model_is_local: bool = False,
    ) -> None:
        self._sources = source_repository
        self._fragments = fragment_repository
        self._lexical = lexical_search
        self._knowledge = knowledge_service
        self._records = record_service
        self._workspaces = workspace_repository
        self._activity = activity_service
        self._privacy = privacy_service
        self._model_is_local = model_is_local

    def search_sources(self, query: str, limit: int = 5) -> str:
        """Find source fragments by exact terms and return their provenance."""
        try:
            hits = self._lexical.search(query, limit=self._limit(limit))
        except InvalidSearchQueryError:
            hits = ()
        filename_stem = Path(query).stem if Path(query).suffix.casefold() in {".md", ".txt", ".csv", ".eml", ".pdf"} else query
        normalized = re.sub(r"[^\w]+", " ", filename_stem.replace("_", " ")).strip()
        if not hits and normalized and normalized != query:
            # Models often pass filenames such as COURSE_DETAILS.md. FTS5 parses
            # punctuation as query syntax (or returns no match), so retry the
            # meaningful filename words.
            hits = self._lexical.search(normalized, limit=self._limit(limit))
        elif not hits and not normalized:
            return self._json({"error": "Provide at least one searchable source term."})
        return self._json(
            [
                {
                    "source_id": hit.source.id,
                    "filename": hit.source.path.name,
                    "fragment_id": hit.fragment.id,
                    "heading": hit.fragment.heading,
                    "location": hit.fragment.location,
                    "text": hit.fragment.text,
                    "score": hit.score,
                }
                for hit in hits
                if self._permits_model(hit.source.id)
            ]
        )

    def read_source(self, source_id: int) -> str:
        """Read one registered source's metadata and extracted fragments."""
        source = self._sources.get_by_id(source_id)
        if source is None:
            return self._json({"error": f"Source {source_id} was not found."})
        if not self._permits_model(source.id):
            return self._json(
                {"error": "This source is unavailable or its privacy rule prevents use by the selected model."}
            )
        return self._json(
            {
                "id": source.id,
                "filename": source.path.name,
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
        claims = []
        candidate_claims = self._knowledge.list_claims(concept.id or 0) if concept else []
        for claim in candidate_claims:
            evidence_ids = self._knowledge.evidence_fragment_ids(claim.id or 0)
            evidence = [self._fragments.get(identifier) for identifier in evidence_ids]
            if not evidence or any(part is None or not self._permits_model(part.source_id) for part in evidence):
                continue
            reviews = []
            for review in self._knowledge.accepted_reviews(claim.id or 0):
                part = self._fragments.get(review.fragment_id)
                if part is None or not self._permits_model(part.source_id):
                    continue
                reviews.append({
                    "operation": review.operation.value, "rationale": review.rationale,
                    "fragment_id": part.id, "source_id": part.source_id,
                    "location": part.location, "text": part.text,
                })
            claims.append({
                "id": claim.id, "text": claim.text, "created_at": claim.created_at.isoformat(),
                "evidence_fragment_ids": evidence_ids, "accepted_reviews": reviews,
                "review_caveat": "Reviews record user assessment, not proven truth. Claims are unchanged. Respect contradictions and qualifications. Evidence may be withheld by privacy policy.",
            })
        if candidate_claims and not claims:
            return self._json({"concept": None})
        return self._json(
            {
                "concept": None
                if concept is None
                else {
                    "id": concept.id,
                    "name": concept.name,
                    "created_at": concept.created_at.isoformat(),
                    "claims": claims,
                }
            }
        )

    def search_records(self, query: str, limit: int = 10) -> str:
        """Find saved travel, receipt, and warranty records by their known fields."""
        needle = query.casefold().strip()
        records = []
        for record in self._records.list_travel_records():
            if not self._permits_model(record.source_id):
                continue
            values = (record.flight_number, record.departure, record.arrival, record.booking_reference)
            if any(needle in value.casefold() for value in values if value):
                records.append(
                    {
                        "record_type": "travel",
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
        for record in self._records.list_receipt_records():
            if not self._permits_model(record.source_id):
                continue
            values = (record.merchant, record.currency, record.receipt_number)
            if any(needle in value.casefold() for value in values if value):
                records.append(
                    {
                        "record_type": "receipt",
                        "id": record.id,
                        "source_id": record.source_id,
                        "merchant": record.merchant,
                        "total_cents": record.total_cents,
                        "currency": record.currency,
                        "purchased_at": record.purchased_at.isoformat() if record.purchased_at else None,
                        "receipt_number": record.receipt_number,
                    }
                )
        for record in self._records.list_warranty_records():
            if not self._permits_model(record.source_id):
                continue
            values = (record.product_name, record.provider, record.warranty_number)
            if any(needle in value.casefold() for value in values if value):
                records.append(
                    {
                        "record_type": "warranty",
                        "id": record.id,
                        "source_id": record.source_id,
                        "product_name": record.product_name,
                        "provider": record.provider,
                        "warranty_number": record.warranty_number,
                        "coverage_ends_at": record.coverage_ends_at.isoformat() if record.coverage_ends_at else None,
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
                    "details": self._safe_activity_details(event.details),
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
        """Keep model-provided result counts within the read-only tool budget.

        A tool call is model output, not a trusted API request.  Small local
        models in particular sometimes emit ``0`` or an overly large value
        despite the JSON schema.  Bounding it keeps a harmless argument error
        from aborting the entire agent loop, while still preventing an
        unexpectedly large result from being sent back into model context.
        """
        return max(1, min(value, 20))

    def _permits_model(self, source_id: int | None) -> bool:
        """Enforce the policy for the model that will receive tool output."""

        source = self._sources.get_by_id(source_id) if source_id is not None else None
        if source is None or source.status.value != "active":
            return False
        if self._privacy is None:
            return True
        return (
            self._privacy.permits_local_model(source_id)
            if self._model_is_local
            else self._privacy.permits_external_model(source_id)
        )

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
        """Find locally saved travel, receipt, or warranty records by their known fields."""
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
