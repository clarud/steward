"""Reviewable reconciliation of unambiguous external source moves."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from steward.sources.models import SourceStatus
from steward.sources.repository import SourceRepository


@dataclass(frozen=True, slots=True)
class SourceMoveProposal:
    id: int | None
    missing_source_id: int
    discovered_source_id: int
    content_hash: str
    status: str
    created_at: datetime
    reviewed_at: datetime | None = None


class SourceMoveProposalRepository:
    def __init__(self, database_path: Path) -> None:
        self._database_path = database_path

    def create_pending(self, missing_source_id: int, discovered_source_id: int, content_hash: str) -> SourceMoveProposal:
        created_at = datetime.now(UTC)
        with sqlite3.connect(self._database_path) as connection:
            try:
                cursor = connection.execute(
                    """INSERT INTO source_move_proposals
                       (missing_source_id, discovered_source_id, content_hash, status, created_at)
                       VALUES (?, ?, ?, 'pending', ?)""",
                    (missing_source_id, discovered_source_id, content_hash, created_at.isoformat()),
                )
            except sqlite3.IntegrityError:
                row = connection.execute(
                    """SELECT id, missing_source_id, discovered_source_id, content_hash, status, created_at, reviewed_at
                       FROM source_move_proposals WHERE missing_source_id = ? OR discovered_source_id = ?""",
                    (missing_source_id, discovered_source_id),
                ).fetchone()
                if row is None:
                    raise
                return self._from_row(row)
        return SourceMoveProposal(int(cursor.lastrowid), missing_source_id, discovered_source_id, content_hash, "pending", created_at)

    def list_pending(self) -> tuple[SourceMoveProposal, ...]:
        with sqlite3.connect(self._database_path) as connection:
            rows = connection.execute(
                """SELECT id, missing_source_id, discovered_source_id, content_hash, status, created_at, reviewed_at
                   FROM source_move_proposals WHERE status = 'pending' ORDER BY id"""
            ).fetchall()
        return tuple(self._from_row(row) for row in rows)

    def get(self, proposal_id: int) -> SourceMoveProposal | None:
        with sqlite3.connect(self._database_path) as connection:
            row = connection.execute(
                """SELECT id, missing_source_id, discovered_source_id, content_hash, status, created_at, reviewed_at
                   FROM source_move_proposals WHERE id = ?""", (proposal_id,)
            ).fetchone()
        return self._from_row(row) if row is not None else None

    def review(self, proposal_id: int, status: str) -> SourceMoveProposal:
        if status not in {"accepted", "rejected"}:
            raise ValueError("Move proposal status must be accepted or rejected.")
        proposal = self.get(proposal_id)
        if proposal is None:
            raise ValueError(f"Move proposal {proposal_id} was not found.")
        if proposal.status != "pending":
            return proposal
        reviewed_at = datetime.now(UTC)
        with sqlite3.connect(self._database_path) as connection:
            connection.execute("UPDATE source_move_proposals SET status = ?, reviewed_at = ? WHERE id = ?", (status, reviewed_at.isoformat(), proposal_id))
        return SourceMoveProposal(proposal.id, proposal.missing_source_id, proposal.discovered_source_id, proposal.content_hash, status, proposal.created_at, reviewed_at)

    @staticmethod
    def _from_row(row: tuple[object, ...]) -> SourceMoveProposal:
        return SourceMoveProposal(int(row[0]), int(row[1]), int(row[2]), str(row[3]), str(row[4]), datetime.fromisoformat(str(row[5])), datetime.fromisoformat(str(row[6])) if row[6] else None)


class SourceMoveReconciliationService:
    def __init__(self, sources: SourceRepository, proposals: SourceMoveProposalRepository) -> None:
        self._sources = sources
        self._proposals = proposals

    def propose_for_root(self, root: Path) -> tuple[SourceMoveProposal, ...]:
        """Propose only one-to-one same-root hash matches; never infer ambiguity."""

        resolved_root = root.resolve()
        missing = [item for item in self._sources.list_all() if item.status is SourceStatus.MISSING and item.path.is_relative_to(resolved_root)]
        active = [item for item in self._sources.list_active() if item.path.is_relative_to(resolved_root)]
        created: list[SourceMoveProposal] = []
        for old in missing:
            matches = [new for new in active if new.content_hash == old.content_hash]
            old_matches = [prior for prior in missing if prior.content_hash == old.content_hash]
            if len(matches) == 1 and len(old_matches) == 1 and old.id is not None and matches[0].id is not None:
                created.append(self._proposals.create_pending(old.id, matches[0].id, old.content_hash))
        return tuple(created)

    def accept(self, proposal_id: int):
        proposal = self._proposals.get(proposal_id)
        if proposal is None:
            raise ValueError(f"Move proposal {proposal_id} was not found.")
        if proposal.status == "accepted":
            source = self._sources.get_by_id(proposal.missing_source_id)
            if source is None:
                raise ValueError("The preserved source is no longer registered.")
            return source
        if proposal.status != "pending":
            raise ValueError(f"Move proposal {proposal_id} is already {proposal.status}.")
        source = self._sources.replace_discovered_move(proposal.missing_source_id, proposal.discovered_source_id)
        self._proposals.review(proposal_id, "accepted")
        return source
