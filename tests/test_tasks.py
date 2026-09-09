from datetime import UTC, datetime
from pathlib import Path

import pytest

from steward.storage import initialize_database
from steward.tasks import TaskService


def test_task_service_preserves_a_due_phrase_without_guessing_a_time() -> None:
    assert TaskService.parse_proposal("remind me to compare OpenMP scheduling before Tuesday") == (
        "compare OpenMP scheduling", "before Tuesday"
    )


def test_task_service_accepts_deadline_prefix_and_due_language() -> None:
    assert TaskService.parse_proposal("deadline: submit CS3210 lab due Friday") == (
        "submit CS3210 lab", "due Friday"
    )


def test_task_service_accepts_only_explicit_offset_aware_deadlines() -> None:
    title, due_hint, due_at = TaskService.parse_proposal_with_due_at(
        "deadline: submit CS3210 lab --due-at 2026-09-18T23:59:00+08:00"
    )

    assert (title, due_hint) == ("submit CS3210 lab", None)
    assert due_at == datetime(2026, 9, 18, 15, 59, tzinfo=UTC)
    with pytest.raises(ValueError, match="UTC offset"):
        TaskService.parse_proposal_with_due_at("task: submit lab --due-at 2026-09-18T23:59:00")


def test_task_service_creates_and_lists_open_tasks(tmp_path: Path) -> None:
    database = tmp_path / "steward.db"; initialize_database(database)
    service = TaskService(database)

    task = service.create("Compare OpenMP scheduling", "before Tuesday")

    assert task.id == 1
    assert service.list_open() == (task,)
    completed = service.complete(task.id or 0)
    assert completed.status == "completed"
    assert service.list_open() == ()
    assert service.complete(task.id or 0).status == "completed"


def test_task_service_persists_explicit_deadlines_as_utc_instants(tmp_path: Path) -> None:
    database = tmp_path / "steward.db"; initialize_database(database)
    service = TaskService(database)

    task = service.create("Submit CS3210 lab", due_at=datetime(2026, 9, 18, 23, 59, tzinfo=UTC))

    assert task.due_at == datetime(2026, 9, 18, 23, 59, tzinfo=UTC)
    assert service.list_open() == (task,)


def test_task_service_requires_a_title() -> None:
    with pytest.raises(ValueError, match="task"):
        TaskService.parse_proposal("remind me to")
