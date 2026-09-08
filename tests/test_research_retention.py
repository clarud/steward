from pathlib import Path

from steward.capture import InboxCaptureService
from steward.extraction import SourceFragmentRepository
from steward.research import ResearchBundle, ResearchRetentionService, ResearchSource
from steward.sources import SourceRepository
from steward.storage import initialize_database


def test_retain_research_writes_a_labeled_provenance_note_and_is_idempotent(tmp_path: Path) -> None:
    database_path = tmp_path / "steward.db"
    initialize_database(database_path)
    capture = InboxCaptureService(
        tmp_path / "vault" / "inbox",
        SourceRepository(database_path),
        SourceFragmentRepository(database_path),
    )
    bundle = ResearchBundle(
        "TLB shootdowns",
        "A kernel invalidates stale translations.",
        (ResearchSource("Kernel docs", "https://example.com/tlb"),),
    )
    service = ResearchRetentionService(capture)

    first = service.retain(bundle)
    repeated = service.retain(bundle)

    content = first.source.path.read_text(encoding="utf-8")
    assert first.duplicate is False
    assert repeated.duplicate is True
    assert "user-retained model-generated research note" in content
    assert "https://example.com/tlb" in content
    fragments = SourceFragmentRepository(database_path).list_for_source(first.source.id or 0)
    assert any("kernel invalidates" in fragment.text.casefold() for fragment in fragments)
