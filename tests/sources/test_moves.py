from dataclasses import replace
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
    assert sources.location_history(first.id or 0) == (original.resolve(),)
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


def test_move_acceptance_refuses_a_path_that_left_the_reviewed_root(tmp_path: Path) -> None:
    database = tmp_path / "steward.db"; initialize_database(database)
    first_root = tmp_path / "first"; first_root.mkdir()
    second_root = tmp_path / "second"; second_root.mkdir()
    original = first_root / "first.md"; original.write_text("same bytes", encoding="utf-8")
    sources = SourceRepository(database)
    scanner = SourceService(sources, SourceFragmentRepository(database), MarkdownExtractor())
    scanner.scan_source_root(first_root)
    original.rename(first_root / "renamed.md")
    scanner.scan_source_root(first_root)
    moves = SourceMoveReconciliationService(sources, SourceMoveProposalRepository(database))
    proposal = moves.propose_for_root(first_root)[0]

    moved = first_root / "renamed.md"
    moved.rename(second_root / moved.name)
    discovered = sources.get_by_id(proposal.discovered_source_id)
    assert discovered is not None
    sources.update(replace(discovered, path=second_root / moved.name))

    try:
        moves.accept(proposal.id or 0)
    except ValueError as error:
        assert "reviewed root" in str(error)
    else:
        raise AssertionError("A cross-root source must not preserve a same-root move identity.")
