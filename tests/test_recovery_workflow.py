"""Recovery rehearsals over synthetic state, never a user's operational database."""

from datetime import UTC, datetime
from pathlib import Path
import json
import subprocess
import sys

from steward.sources import Source, SourceRepository, SourceType
from steward.sources.hashing import hash_file
from steward.storage import initialize_database, restore_database, snapshot_database


def test_registered_file_survives_backup_restore_and_new_process(tmp_path: Path) -> None:
    database = tmp_path / "active.db"
    initialize_database(database)
    original = tmp_path / "queues.md"
    original.write_text("Queues may buffer jobs.\n", encoding="utf-8")
    original_bytes = original.read_bytes()
    now = datetime.now(UTC)
    source = SourceRepository(database).add(Source(None, original, hash_file(original),
        SourceType.MARKDOWN, len(original_bytes), now, now, now))
    snapshot = snapshot_database(database, tmp_path / "backup.db")
    SourceRepository(database).unregister(source.id or 0)

    safety = restore_database(snapshot, database, tmp_path / "before-restore.db")
    assert SourceRepository(safety).get_by_id(source.id or 0) is None
    assert SourceRepository(database).get_by_id(source.id or 0) == source

    # A fresh interpreter cannot rely on any in-memory repository state.
    script = """
import json, sys
from pathlib import Path
from steward.sources import SourceRepository
found = SourceRepository(Path(sys.argv[1])).get_by_id(int(sys.argv[2]))
print(json.dumps({'name': found.path.name if found else None}))
"""
    completed = subprocess.run([sys.executable, "-c", script, str(database), str(source.id)],
                               capture_output=True, text=True, timeout=20, check=True)
    assert json.loads(completed.stdout) == {"name": "queues.md"}
    assert original.read_bytes() == original_bytes
