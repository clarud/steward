"""Recovery rehearsals over synthetic state, never a user's operational database."""

from datetime import UTC, datetime
from pathlib import Path
import json
import subprocess
import sys

from steward.action_proposals import ActionProposalRepository
from steward.activity import ActivityService
from steward.sources import Source, SourceRepository, SourceType
from steward.sources.hashing import hash_file
from steward.storage import initialize_database, restore_database, snapshot_database


def test_pending_privacy_review_survives_backup_restore_and_new_process(tmp_path: Path) -> None:
    database = tmp_path / "active.db"
    initialize_database(database)
    original = tmp_path / "queues.md"
    original.write_text("Queues may buffer jobs.\n", encoding="utf-8")
    original_bytes = original.read_bytes()
    now = datetime.now(UTC)
    source = SourceRepository(database).add(Source(None, original, hash_file(original),
        SourceType.MARKDOWN, len(original_bytes), now, now, now))
    repository = ActionProposalRepository(database)
    pending = repository.add("set_source_privacy", {"source_id": str(source.id), "rule": "local_only"})
    snapshot = snapshot_database(database, tmp_path / "pending-backup.db")
    repository.set_status(pending.id or 0, "rejected")

    safety = restore_database(snapshot, database, tmp_path / "before-restore.db")
    assert ActionProposalRepository(safety).get(pending.id or 0).status == "rejected"
    assert ActionProposalRepository(database).get(pending.id or 0) == pending
    assert ActivityService(database).list_recent() == []

    # A fresh interpreter cannot rely on any in-memory repository state.
    script = """
import json, sys
from pathlib import Path
from steward.action_proposals import ActionProposalRepository
db = Path(sys.argv[1])
repository = ActionProposalRepository(db)
repository.set_status(int(sys.argv[2]), 'accepted')
print(json.dumps({'status': repository.get(int(sys.argv[2])).status}))
"""
    completed = subprocess.run([sys.executable, "-c", script, str(database), str(pending.id)],
                               capture_output=True, text=True, timeout=20, check=True)
    assert json.loads(completed.stdout) == {"status": "accepted"}
    assert ActionProposalRepository(snapshot).get(pending.id or 0).status == "pending"
    assert ActionProposalRepository(safety).get(pending.id or 0).status == "rejected"
    assert original.read_bytes() == original_bytes
