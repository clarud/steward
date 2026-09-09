from datetime import UTC, datetime
from pathlib import Path

import pytest

from steward.activity import ActivityService, ActivityType
from steward.storage import initialize_database
from steward.tasks import TaskReminderService, TaskService


def test_task_service_preserves_a_due_phrase_without_guessing_a_time() -> None:
    assert TaskService.parse_proposal("remind me to compare OpenMP scheduling before Tuesday") == (
        "compare OpenMP scheduling", "before Tuesday"
    )


def test_task_service_accepts_deadline_prefix_and_due_language() -> None:
    assert TaskService.parse_proposal("deadline: submit CS3210 lab due Friday") == (
        "submit CS3210 lab", "due Friday"
    )


def test_task_service_accepts_a_plain_personal_commitment_prefix() -> None:
    assert TaskService.parse_proposal("I need to submit CS3210 lab by Friday") == (
        "submit CS3210 lab", "by Friday"
    )


def test_task_service_accepts_only_explicit_offset_aware_deadlines() -> None:
    title, due_hint, due_at = TaskService.parse_proposal_with_due_at(
        "deadline: submit CS3210 lab --due-at 2026-09-18T23:59:00+08:00"
    )

    assert (title, due_hint) == ("submit CS3210 lab", None)
    assert due_at == datetime(2026, 9, 18, 15, 59, tzinfo=UTC)
    with pytest.raises(ValueError, match="UTC offset"):
        TaskService.parse_proposal_with_due_at("task: submit lab --due-at 2026-09-18T23:59:00")


def test_task_service_parses_an_explicit_reminder_without_guessing_one() -> None:
    title, due_hint, due_at, remind_at = TaskService.parse_proposal_with_schedule(
        "task: submit CS3210 lab --due-at 2026-09-18T23:59:00+08:00 "
        "--remind-at 2026-09-18T09:00:00+08:00"
    )

    assert (title, due_hint) == ("submit CS3210 lab", None)
    assert due_at == datetime(2026, 9, 18, 15, 59, tzinfo=UTC)
    assert remind_at == datetime(2026, 9, 18, 1, 0, tzinfo=UTC)


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


def test_task_reminder_claims_retries_and_acknowledges_due_delivery(tmp_path: Path) -> None:
    database = tmp_path / "steward.db"; initialize_database(database)
    tasks = TaskService(database); activity = ActivityService(database)
    reminder_service = TaskReminderService(database, tasks, activity)
    task = tasks.create("Submit CS3210 lab")
    scheduled = reminder_service.schedule(
        task.id or 0, "100", datetime(2026, 9, 18, 9, tzinfo=UTC)
    )

    assert reminder_service.claim_due(datetime(2026, 9, 18, 8, 59, tzinfo=UTC)) == ()
    claimed = reminder_service.claim_due(datetime(2026, 9, 18, 9, tzinfo=UTC))
    assert claimed == (scheduled,)
    reminder_service.release(task.id or 0)
    assert reminder_service.claim_due(datetime(2026, 9, 18, 9, 1, tzinfo=UTC)) == (scheduled,)
    reminder_service.acknowledge(task.id or 0, datetime(2026, 9, 18, 9, 2, tzinfo=UTC))

    assert reminder_service.claim_due(datetime(2026, 9, 18, 9, 10, tzinfo=UTC)) == ()
    assert activity.list_recent()[0].event_type is ActivityType.TASK_REMINDER_SENT


def test_task_service_requires_a_title() -> None:
    with pytest.raises(ValueError, match="task"):
        TaskService.parse_proposal("remind me to")
