from pathlib import Path

import pytest

from steward.storage import initialize_database
from steward.tasks import TaskService


def test_task_service_preserves_a_due_phrase_without_guessing_a_time() -> None:
    assert TaskService.parse_proposal("remind me to compare OpenMP scheduling before Tuesday") == (
        "compare OpenMP scheduling", "before Tuesday"
    )


def test_task_service_creates_and_lists_open_tasks(tmp_path: Path) -> None:
    database = tmp_path / "steward.db"; initialize_database(database)
    service = TaskService(database)

    task = service.create("Compare OpenMP scheduling", "before Tuesday")

    assert task.id == 1
    assert service.list_open() == (task,)


def test_task_service_requires_a_title() -> None:
    with pytest.raises(ValueError, match="task"):
        TaskService.parse_proposal("remind me to")
