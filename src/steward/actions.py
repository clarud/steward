"""Safe, reversible local mutations with explicit rollback information."""
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
from shutil import move
from dataclasses import replace
from steward.activity import ActivityService, ActivityType
from steward.sources import Source, SourceRepository

@dataclass(frozen=True, slots=True)
class ActionResult:
    status: str
    object_id: str
    rollback_data: dict[str, str]
    activity_event_id: int | None = None

class FileMutationService:
    def __init__(self, source_repository: SourceRepository | None = None, activity_service: ActivityService | None = None) -> None:
        self._source_repository = source_repository
        self._activity_service = activity_service
    def move_source(self, source_path: Path, destination: Path) -> ActionResult:
        if not source_path.is_file(): raise FileNotFoundError(source_path)
        if destination.exists(): raise FileExistsError(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        move(str(source_path), str(destination))
        source = self._source_repository.get_by_path(source_path.resolve()) if self._source_repository else None
        if source is not None:
            self._source_repository.update(replace(source, path=destination.resolve()))
        event_id = None
        if self._activity_service is not None:
            event_id = self._activity_service.record(ActivityType.SOURCE_MOVED, object_id=str(source.id) if source else None, details=f"{source_path} -> {destination}").id
        return ActionResult("moved", str(destination), {"source_path": str(source_path), "destination": str(destination)}, event_id)
    def undo_move(self, result: ActionResult) -> ActionResult:
        if result.status != "moved": raise ValueError("Only a move action can be undone.")
        destination = Path(result.rollback_data["destination"]); source_path = Path(result.rollback_data["source_path"])
        if not destination.is_file(): raise FileNotFoundError(destination)
        if source_path.exists(): raise FileExistsError(source_path)
        source_path.parent.mkdir(parents=True, exist_ok=True)
        move(str(destination), str(source_path))
        source = self._source_repository.get_by_path(destination.resolve()) if self._source_repository else None
        if source is not None:
            self._source_repository.update(replace(source, path=source_path.resolve()))
        event_id = None
        if self._activity_service is not None:
            event_id = self._activity_service.record(
                ActivityType.SOURCE_MOVE_UNDONE,
                object_id=str(source.id) if source else None,
                details=f"{destination} -> {source_path}",
            ).id
        return ActionResult(
            "undone",
            str(source_path),
            {"source_path": str(source_path), "destination": str(destination)},
            event_id,
        )
