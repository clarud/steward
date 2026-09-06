from pathlib import Path

from steward.extraction import MarkdownExtractor, SourceFragmentRepository
from steward.sources import SourceRepository
from steward.sources.service import SourceService
from steward.storage import initialize_database


def test_source_service_refreshes_fragments_when_a_source_changes(tmp_path: Path) -> None:
    database_path = tmp_path / "steward.db"
    initialize_database(database_path)
    vault = tmp_path / "vault"
    vault.mkdir()
    source_path = vault / "note.md"
    source_path.write_text("# First\nOriginal text.", encoding="utf-8")
    source_repository = SourceRepository(database_path)
    fragment_repository = SourceFragmentRepository(database_path)
    service = SourceService(
        source_repository=source_repository,
        fragment_repository=fragment_repository,
        markdown_extractor=MarkdownExtractor(),
    )
    service.scan_markdown_root(vault)
    source = source_repository.get_by_path(source_path.resolve())
    assert source is not None
    source_path.write_text("# Second\nUpdated text.", encoding="utf-8")

    service.scan_markdown_root(vault)

    fragments = fragment_repository.list_for_source(source.id or 0)
    assert [fragment.heading for fragment in fragments] == ["Second"]
    assert [fragment.text for fragment in fragments] == ["# Second\nUpdated text."]
