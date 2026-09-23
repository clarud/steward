from datetime import UTC, datetime
from hashlib import sha256

import pytest

from steward.sources import Source, SourceRepository, SourceType
from steward.sources.export import SourceExportService
from steward.roots import SourceRootRepository
from steward.storage import initialize_database


def test_original_export_enforces_roots_exclusions_size_and_freshness(tmp_path):
    database = tmp_path / "db.sqlite"
    initialize_database(database)
    root = tmp_path / "vault"
    root.mkdir()
    path = root / "notes.pdf"
    content = b"original pdf bytes"
    path.write_bytes(content)
    now = datetime.now(UTC)
    sources = SourceRepository(database)
    source = sources.add(Source(None, path, sha256(content).hexdigest(), SourceType.PDF, len(content), now, now, now))
    roots = SourceRootRepository(database)
    service = SourceExportService(sources, roots, tmp_path / "inbox")
    with pytest.raises(ValueError, match="outside"):
        service.export(source.id)
    roots.add("Notes", root)
    exported = service.export(source.id)
    assert exported.filename == "notes.pdf" and exported.content == content
    from steward.app import StewardFilesApplication
    from steward.extraction import SourceFragmentRepository
    from steward.events import IncomingEvent
    files = StewardFilesApplication(sources, SourceFragmentRepository(database), roots, tmp_path / "inbox", source_export=service)
    card = files.file_card(source.id)
    send = next(action.command for action in card.actions if action.label == "Send original")
    delivered = files.handle_command(IncomingEvent("request-1", "telegram", "100", "1", None, now, send))
    assert delivered.document.content == content
    path.write_bytes(b"changed")
    with pytest.raises(ValueError, match="changed"):
        service.export(source.id)
    service.MAX_BYTES = 3
    with pytest.raises(ValueError, match="limit"):
        service.export(source.id)
    # Exclusions apply even when another root would otherwise allow access.
    roots.add("Parent", tmp_path, exclusions=(root,))
    with pytest.raises(ValueError, match="excluded"):
        service.export(source.id)
