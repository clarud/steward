import json
from datetime import UTC, datetime
from pathlib import Path

from steward.roots import SourceRootRepository
from steward.sources import CodexHandoffService, Source, SourceRepository, SourceType
from steward.sources.hashing import hash_file
from steward.storage import initialize_database


def test_handoff_is_local_metadata_only_and_includes_root_guidance(tmp_path: Path) -> None:
    database = tmp_path / "steward.db"; initialize_database(database)
    root = tmp_path / "course"; root.mkdir(); (root / "AGENTS.md").write_text("guidance", encoding="utf-8")
    source_path = root / "lecture.md"; source_path.write_text("private source text", encoding="utf-8")
    now = datetime.now(UTC)
    source = SourceRepository(database).add(Source(None, source_path.resolve(), hash_file(source_path), SourceType.MARKDOWN, source_path.stat().st_size, now, now, now))
    roots = SourceRootRepository(database); roots.add("Course", root)

    handoff = CodexHandoffService(SourceRepository(database), roots, tmp_path / ".steward", tmp_path / "inbox").prepare((source.id or 0,), note="Organize lecture notes")

    payload = json.loads(handoff.path.read_text(encoding="utf-8"))
    assert payload["sources"][0]["source_id"] == source.id
    assert payload["sources"][0]["root_relative_path"] == "lecture.md"
    assert "private source text" not in handoff.path.read_text(encoding="utf-8")
    assert str(root / "AGENTS.md") in payload["guidance_document_paths"]
