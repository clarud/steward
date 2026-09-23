from pathlib import Path

from steward.extraction import MarkdownExtractor, SourceFragmentRepository
from steward.sources import (
    SourceMoveProposalRepository,
    SourceMoveReconciliationService,
    SourceRepository,
)
from steward.sources.service import SourceService
from steward.storage import initialize_database


def test_user_accepted_unambiguous_move_preserves_original_source_id(tmp_path: Path) -> None:
    database = tmp_path / "steward.db"; initialize_database(database)
    root = tmp_path / "notes"; root.mkdir()
    original = root / "first.md"; original.write_text("# Queueing\nLittle's law", encoding="utf-8")
    sources = SourceRepository(database)
    service = SourceService(sources, SourceFragmentRepository(database), MarkdownExtractor())
    service.scan_source_root(root)
    first = sources.get_by_path(original.resolve())
    assert first is not None

    moved = root / "lectures" / "queueing.md"; moved.parent.mkdir(); original.rename(moved)
    service.scan_source_root(root)
    discovered = sources.get_by_path(moved.resolve())
    assert discovered is not None and discovered.id != first.id

    moves = SourceMoveReconciliationService(sources, SourceMoveProposalRepository(database))
    proposals = moves.propose_for_root(root)
    assert len(proposals) == 1

    preserved = moves.accept(proposals[0].id or 0)

    assert preserved.id == first.id
    assert preserved.path == moved.resolve()
    assert sources.get_by_id(discovered.id or 0) is None
    assert SourceFragmentRepository(database).list_for_source(first.id or 0)[0].heading == "Queueing"


def test_duplicate_content_never_produces_an_automatic_move_match(tmp_path: Path) -> None:
    database = tmp_path / "steward.db"; initialize_database(database)
    root = tmp_path / "notes"; root.mkdir()
    original = root / "first.md"; original.write_text("same bytes", encoding="utf-8")
    sources = SourceRepository(database)
    service = SourceService(sources, SourceFragmentRepository(database), MarkdownExtractor())
    service.scan_source_root(root)

    original.unlink()
    (root / "copy-a.md").write_text("same bytes", encoding="utf-8")
    (root / "copy-b.md").write_text("same bytes", encoding="utf-8")
    service.scan_source_root(root)

    proposals = SourceMoveReconciliationService(
        sources, SourceMoveProposalRepository(database)
    ).propose_for_root(root)

    assert proposals == ()
