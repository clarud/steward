import json
from datetime import UTC, datetime
from pathlib import Path

from steward.roots import SourceRootRepository
from steward.sources import CodexHandoffService, Source, SourceRepository, SourceType
from steward.sources.hashing import hash_file
from steward.sources.inbox_context import SourceInboxContext, SourceInboxContextRepository
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


def test_handoff_retains_inbox_intent_without_moving_or_reading_the_source(tmp_path: Path) -> None:
    database = tmp_path / "steward.db"; initialize_database(database)
    root = tmp_path / "Y4S1"; root.mkdir(); (root / "AGENTS.md").write_text("guidance", encoding="utf-8")
    inbox = tmp_path / "inbox"; inbox.mkdir()
    source_path = inbox / "uploaded.pdf"; source_path.write_text("private source text", encoding="utf-8")
    now = datetime.now(UTC)
    source = SourceRepository(database).add(Source(
        None, source_path.resolve(), hash_file(source_path), SourceType.PDF,
        source_path.stat().st_size, now, now, now,
    ))
    roots = SourceRootRepository(database); intended = roots.add("Y4S1", root)
    contexts = SourceInboxContextRepository(database)
    contexts.set(SourceInboxContext(source.id or 0, intended.id, intended.name, "CS3210 lecture", "telegram", now))

    handoff = CodexHandoffService(
        SourceRepository(database), roots, tmp_path / ".steward", inbox, contexts
    ).prepare((source.id or 0,))
    payload = json.loads(handoff.path.read_text(encoding="utf-8"))

    context = payload["sources"][0]["inbox_capture_context"]
    assert context == {"intended_root": "Y4S1", "user_context": "CS3210 lecture", "capture_origin": "telegram"}
    assert str(root / "AGENTS.md") in payload["guidance_document_paths"]
    assert "private source text" not in handoff.path.read_text(encoding="utf-8")
