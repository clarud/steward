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
    from steward.application import StewardReadApplication
    from steward.extraction import SourceFragmentRepository
    from steward.retrieval import LexicalSearchService
    from steward.workspaces import WorkspaceRepository
    from steward.activity import ActivityService
    fragments = SourceFragmentRepository(database)
    reader = StewardReadApplication(sources, fragments, LexicalSearchService(sources, fragments), WorkspaceRepository(database), ActivityService(database), tmp_path / "inbox", source_export=service)
    assert any(action.command == f"/send_source {source.id}" for action in reader.source(source.id).actions)
    roots.set_enabled("Notes", False)
    with pytest.raises(ValueError, match="outside"):
        service.export(source.id)
    roots.set_enabled("Notes", True)
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
