"""Durable, reviewable proposals for state changes such as a source privacy rule."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime



@dataclass(frozen=True, slots=True)
class ActionProposal:
    """A requested action that has not been authorized for execution yet."""

    id: int | None
    action_type: str
    payload: dict[str, str]
    status: str
    created_at: datetime
    reviewed_at: datetime | None = None


class ActionProposalRepository:
    """SQLite persistence for generic action proposals."""

    def __init__(self, database_path) -> None:
        self._database_path = database_path

    def add(self, action_type: str, payload: dict[str, str]) -> ActionProposal:
        created_at = datetime.now(UTC)
        payload_json = json.dumps(payload, sort_keys=True)
        with sqlite3.connect(self._database_path) as connection:
            cursor = connection.execute(
                "INSERT INTO action_proposals (action_type, payload_json, status, created_at) "
                "VALUES (?, ?, 'pending', ?)",
                (action_type, payload_json, created_at.isoformat()),
            )
        return ActionProposal(int(cursor.lastrowid), action_type, payload, "pending", created_at)

    def find_pending(self, action_type: str, payload: dict[str, str]) -> ActionProposal | None:
        payload_json = json.dumps(payload, sort_keys=True)
        with sqlite3.connect(self._database_path) as connection:
            row = connection.execute(
                "SELECT id, action_type, payload_json, status, created_at, reviewed_at "
                "FROM action_proposals WHERE action_type = ? AND payload_json = ? AND status = 'pending'",
                (action_type, payload_json),
            ).fetchone()
        return self._from_row(row) if row is not None else None

    def get(self, proposal_id: int) -> ActionProposal | None:
        with sqlite3.connect(self._database_path) as connection:
            row = connection.execute(
                "SELECT id, action_type, payload_json, status, created_at, reviewed_at "
                "FROM action_proposals WHERE id = ?",
                (proposal_id,),
            ).fetchone()
        return self._from_row(row) if row is not None else None

    def list_all(self) -> list[ActionProposal]:
        with sqlite3.connect(self._database_path) as connection:
            rows = connection.execute(
                "SELECT id, action_type, payload_json, status, created_at, reviewed_at "
                "FROM action_proposals ORDER BY id"
            ).fetchall()
        return [self._from_row(row) for row in rows]

    def set_status(self, proposal_id: int, status: str) -> None:
        if status not in {"accepted", "rejected"}:
            raise ValueError("Action proposal status must be accepted or rejected.")
        with sqlite3.connect(self._database_path) as connection:
            cursor = connection.execute(
                "UPDATE action_proposals SET status = ?, reviewed_at = ? "
                "WHERE id = ? AND status = 'pending'",
                (status, datetime.now(UTC).isoformat(), proposal_id),
            )
        if cursor.rowcount != 1:
            raise ValueError("Action proposal was not found or was already reviewed.")

    def set_status_with_payload(self, proposal_id: int, status: str, payload: dict[str, str]) -> None:
        """Finish one pending proposal while preserving an execution receipt.

        The caller supplies a complete replacement payload rather than an
        unconstrained patch.  This keeps the reviewed proposal's original
        request plus a narrow external ID available for later audit/navigation.
        """

        if status not in {"accepted", "rejected"}:
            raise ValueError("Action proposal status must be accepted or rejected.")
        with sqlite3.connect(self._database_path) as connection:
            cursor = connection.execute(
                "UPDATE action_proposals SET payload_json = ?, status = ?, reviewed_at = ? "
                "WHERE id = ? AND status = 'pending'",
                (json.dumps(payload, sort_keys=True), status, datetime.now(UTC).isoformat(), proposal_id),
            )
        if cursor.rowcount != 1:
            raise ValueError("Action proposal was not found or was already reviewed.")

    @staticmethod
    def _from_row(row: tuple[object, ...]) -> ActionProposal:
        return ActionProposal(
            id=int(row[0]),
            action_type=str(row[1]),
            payload={str(key): str(value) for key, value in json.loads(str(row[2])).items()},
            status=str(row[3]),
            created_at=datetime.fromisoformat(str(row[4])),
            reviewed_at=datetime.fromisoformat(str(row[5])) if row[5] else None,
        )

