"""Recovery rehearsals over synthetic state, never a user's operational database."""

from datetime import UTC, datetime
from pathlib import Path
import json
import subprocess
import sys

from steward.activity import ActivityService
from steward.extraction import ExtractionResult, SourceFragment, SourceFragmentRepository
from steward.knowledge import KnowledgeEnrichmentProposalRepository, KnowledgeService
from steward.sources import Source, SourceRepository, SourceType
from steward.sources.hashing import hash_file
from steward.storage import initialize_database, restore_database, snapshot_database


def test_pending_knowledge_review_survives_backup_restore_and_new_process(tmp_path: Path) -> None:
    database = tmp_path / "active.db"
    initialize_database(database)
    original = tmp_path / "queues.md"
    original.write_text("Queues may buffer jobs.\n", encoding="utf-8")
    original_bytes = original.read_bytes()
    now = datetime.now(UTC)
    source = SourceRepository(database).add(Source(None, original, hash_file(original),
        SourceType.MARKDOWN, len(original_bytes), now, now, now))
    fragment = SourceFragmentRepository(database).replace_for_source(ExtractionResult(source.id, (
        SourceFragment(None, source.id, "Queues", 0, "Queues may buffer jobs.", "line 1"),
    )))[0]
    knowledge = KnowledgeService(database)
    concept = knowledge.create_concept("Queues")
    claim = knowledge.create_claim(concept.id, "Queues buffer jobs.", [fragment.id])
    repository = KnowledgeEnrichmentProposalRepository(database)
    pending = repository.add(knowledge.compare_evidence(claim, fragment_id=fragment.id, evidence_text=fragment.text))
    snapshot = snapshot_database(database, tmp_path / "pending-backup.db")
    repository.review(pending.id, "rejected")

    safety = restore_database(snapshot, database, tmp_path / "before-restore.db")
    assert KnowledgeEnrichmentProposalRepository(safety).get(pending.id).status == "rejected"
    assert KnowledgeEnrichmentProposalRepository(database).get(pending.id) == pending
    assert ActivityService(database).list_recent() == []
    assert len(ActivityService(safety).list_recent()) == 1

    # A fresh interpreter cannot rely on any in-memory repository or graph state.
    script = """
import json, sys
from pathlib import Path
from steward.knowledge import KnowledgeEnrichmentProposalRepository, KnowledgeService
from steward.activity import ActivityService
db = Path(sys.argv[1])
repository = KnowledgeEnrichmentProposalRepository(db)
reviewed = repository.review(int(sys.argv[2]), 'accepted')
print(json.dumps({'status': reviewed.status,
                  'current_reviews': len(KnowledgeService(db).accepted_reviews(reviewed.claim_id)),
                  'audit_events': len(ActivityService(db).list_recent())}))
"""
    completed = subprocess.run([sys.executable, "-c", script, str(database), str(pending.id)],
                               capture_output=True, text=True, timeout=20, check=True)
    assert json.loads(completed.stdout) == {"status": "accepted", "current_reviews": 1, "audit_events": 1}
    assert KnowledgeEnrichmentProposalRepository(snapshot).get(pending.id).status == "pending"
    assert KnowledgeEnrichmentProposalRepository(safety).get(pending.id).status == "rejected"
    assert original.read_bytes() == original_bytes
