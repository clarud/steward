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


def test_source_service_scans_and_extracts_plain_text_files(tmp_path: Path) -> None:
    database_path = tmp_path / "steward.db"; initialize_database(database_path)
    vault = tmp_path / "vault"; vault.mkdir()
    source_path = vault / "todo.txt"; source_path.write_text("Study address translation", encoding="utf-8")
    sources = SourceRepository(database_path)
    fragments = SourceFragmentRepository(database_path)
    service = SourceService(sources, fragments, MarkdownExtractor())

    result = service.scan_source_root(vault)
    source = sources.get_by_path(source_path.resolve())

    assert result.new == 1
    assert source is not None
    assert [fragment.text for fragment in fragments.list_for_source(source.id or 0)] == ["Study address translation"]


def test_source_service_scans_and_extracts_docx_files(tmp_path: Path) -> None:
    from docx import Document

    database_path = tmp_path / "steward.db"; initialize_database(database_path)
    vault = tmp_path / "vault"; vault.mkdir()
    source_path = vault / "notes.docx"
    document = Document(); document.add_heading("TLB", level=1); document.add_paragraph("Caches translations."); document.save(source_path)
    sources = SourceRepository(database_path)
    fragments = SourceFragmentRepository(database_path)
    service = SourceService(sources, fragments, MarkdownExtractor())

    result = service.scan_source_root(vault)
    source = sources.get_by_path(source_path.resolve())

    assert result.new == 1
    assert source is not None
    assert [(fragment.heading, fragment.text) for fragment in fragments.list_for_source(source.id or 0)] == [
        ("TLB", "Caches translations.")
    ]


def test_source_service_keeps_scanning_when_a_document_cannot_be_extracted(tmp_path: Path) -> None:
    database_path = tmp_path / "steward.db"; initialize_database(database_path)
    vault = tmp_path / "vault"; vault.mkdir()
    (vault / "broken.docx").write_bytes(b"not a Word document")
    (vault / "readable.txt").write_text("Still indexed", encoding="utf-8")
    sources = SourceRepository(database_path)
    fragments = SourceFragmentRepository(database_path)
    service = SourceService(sources, fragments, MarkdownExtractor())

    result = service.scan_source_root(vault)

    broken = sources.get_by_path((vault / "broken.docx").resolve())
    readable = sources.get_by_path((vault / "readable.txt").resolve())
    assert result.new == 2
    assert broken is not None
    assert fragments.list_for_source(broken.id or 0) == ()
    assert readable is not None
    assert [fragment.text for fragment in fragments.list_for_source(readable.id or 0)] == ["Still indexed"]


def test_source_service_scans_and_extracts_html_files(tmp_path: Path) -> None:
    database_path = tmp_path / "steward.db"; initialize_database(database_path)
    vault = tmp_path / "vault"; vault.mkdir()
    source_path = vault / "notes.html"; source_path.write_text("<h1>TLB</h1><p>Caches translations.</p>", encoding="utf-8")
    sources = SourceRepository(database_path)
    fragments = SourceFragmentRepository(database_path)
    service = SourceService(sources, fragments, MarkdownExtractor())

    result = service.scan_source_root(vault)
    source = sources.get_by_path(source_path.resolve())

    assert result.new == 1
    assert source is not None
    assert [(fragment.heading, fragment.text) for fragment in fragments.list_for_source(source.id or 0)] == [
        ("TLB", "Caches translations.")
    ]
