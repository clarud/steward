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


def test_task_service_reschedules_only_the_expected_open_deadline(tmp_path: Path) -> None:
    database = tmp_path / "steward.db"; initialize_database(database)
    service = TaskService(database)
    original = service.create("Submit CS3210 lab", due_at=datetime(2026, 9, 18, 15, 59, tzinfo=UTC))

    changed = service.reschedule_due_at(
        original.id or 0,
        datetime(2026, 9, 19, 9, tzinfo=UTC),
        expected_due_at=original.due_at,
    )

    assert changed.due_at == datetime(2026, 9, 19, 9, tzinfo=UTC)
    assert changed.due_hint is None
    with pytest.raises(ValueError, match="changed after"):
        service.reschedule_due_at(
            original.id or 0,
            datetime(2026, 9, 20, 9, tzinfo=UTC),
            expected_due_at=original.due_at,
        )


def test_task_service_clears_only_the_reviewed_open_deadline(tmp_path: Path) -> None:
    database = tmp_path / "steward.db"; initialize_database(database)
    service = TaskService(database)
    original = service.create("Submit CS3210 lab", due_at=datetime(2026, 9, 18, 15, 59, tzinfo=UTC))

    cleared = service.clear_due_at(original.id or 0, expected_due_at=original.due_at)

    assert cleared.due_at is None and cleared.due_hint is None
    with pytest.raises(ValueError, match="changed after"):
        service.clear_due_at(original.id or 0, expected_due_at=original.due_at)


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
    assert claimed[0].task == scheduled.task
    assert claimed[0].claim_token
    reminder_service.release(task.id or 0, claimed[0].claim_token)
    retry = reminder_service.claim_due(datetime(2026, 9, 18, 9, 1, tzinfo=UTC))[0]
    assert retry.claim_token != claimed[0].claim_token
    reminder_service.acknowledge(task.id or 0, retry.claim_token, datetime(2026, 9, 18, 9, 2, tzinfo=UTC))

    assert reminder_service.claim_due(datetime(2026, 9, 18, 9, 10, tzinfo=UTC)) == ()
    assert activity.list_recent()[0].event_type is ActivityType.TASK_REMINDER_SENT


def test_task_reminder_reschedule_is_stale_safe_and_cannot_change_chat(tmp_path: Path) -> None:
    database = tmp_path / "steward.db"; initialize_database(database)
    tasks = TaskService(database); activity = ActivityService(database)
    service = TaskReminderService(database, tasks, activity)
    task = tasks.create("Submit CS3210 lab")
    original = service.schedule(task.id or 0, "100", datetime(2026, 9, 18, 9, tzinfo=UTC))

    changed = service.reschedule(
        task.id or 0, "100", datetime(2026, 9, 18, 10, tzinfo=UTC),
        expected_remind_at=original.remind_at, expected_chat_id="100",
    )

    assert changed.remind_at == datetime(2026, 9, 18, 10, tzinfo=UTC)
    with pytest.raises(ValueError, match="changed after"):
        service.reschedule(
            task.id or 0, "100", datetime(2026, 9, 18, 11, tzinfo=UTC),
            expected_remind_at=original.remind_at, expected_chat_id="100",
        )
    with pytest.raises(ValueError, match="different Telegram chat"):
        service.reschedule(
            task.id or 0, "other", datetime(2026, 9, 18, 11, tzinfo=UTC),
            expected_remind_at=changed.remind_at, expected_chat_id="100",
        )


def test_task_reminder_cancellation_is_stale_safe_owner_bound_and_refuses_delivery_claim(tmp_path: Path) -> None:
    database = tmp_path / "steward.db"; initialize_database(database)
    tasks = TaskService(database); activity = ActivityService(database)
    service = TaskReminderService(database, tasks, activity)
    task = tasks.create("Submit CS3210 lab")
    original = service.schedule(task.id or 0, "100", datetime(2026, 9, 18, 9, tzinfo=UTC))

    cancelled = service.cancel(
        task.id or 0, "100", expected_remind_at=original.remind_at, expected_chat_id="100",
    )

    assert cancelled.id == task.id
    assert service.reminder_for_task(task.id or 0) is None
    service.schedule(task.id or 0, "100", datetime(2026, 9, 18, 10, tzinfo=UTC))
    with pytest.raises(ValueError, match="changed after"):
        service.cancel(
            task.id or 0, "100", expected_remind_at=original.remind_at, expected_chat_id="100",
        )
    claimed = service.claim_due(datetime(2026, 9, 18, 10, tzinfo=UTC))[0]
    with pytest.raises(ValueError, match="currently being delivered"):
        service.cancel(
            task.id or 0, "100", expected_remind_at=claimed.remind_at, expected_chat_id="100",
        )


def test_task_service_requires_a_title() -> None:
    with pytest.raises(ValueError, match="task"):
        TaskService.parse_proposal("remind me to")


@pytest.mark.parametrize("reschedule", [False, True])
def test_stale_reminder_worker_cannot_change_new_claim(tmp_path: Path, reschedule: bool) -> None:
    database = tmp_path / "steward.db"
    initialize_database(database)
    tasks = TaskService(database)
    activity = ActivityService(database)
    service = TaskReminderService(database, tasks, activity)
    task_id = tasks.create("Review notes").id
    now = datetime(2026, 9, 18, 9, tzinfo=UTC)
    service.schedule(task_id, "100", now)
    old = service.claim_due(now)[0]
    if reschedule:
        service.schedule(task_id, "100", now)
    later = datetime(2026, 9, 18, 9, 6, tzinfo=UTC)
    replacement = TaskReminderService(database, tasks, activity)
    new = replacement.claim_due(later)[0]
    assert new.claim_token != old.claim_token
    service.release(task_id, old.claim_token)
    assert replacement.claim_due(later) == ()
    service.acknowledge(task_id, old.claim_token, later)
    assert replacement.reminder_for_task(task_id) is not None
    assert activity.list_recent() == []
    replacement.acknowledge(task_id, new.claim_token, later)
    assert replacement.reminder_for_task(task_id) is None
