import json
from datetime import UTC, datetime
from pathlib import Path
from dataclasses import replace

from steward.activity import ActivityService, ActivityType
from steward.extraction import ExtractionResult, SourceFragment, SourceFragmentRepository
from steward.privacy import PrivacyRule, PrivacyService
from steward.retrieval import LexicalSearchService
from steward.sources import Source, SourceRepository, SourceType, SourceStatus
from steward.storage import initialize_database
from steward.tools import SourceReadOnlyToolService, build_source_read_only_tools

NOW = datetime(2026, 9, 8, tzinfo=UTC)


def _service(
    database: Path, *, privacy: PrivacyService | None = None, model_is_local: bool = False,
) -> SourceReadOnlyToolService:
    sources = SourceRepository(database)
    fragments = SourceFragmentRepository(database)
    return SourceReadOnlyToolService(
        sources, fragments, LexicalSearchService(sources, fragments), ActivityService(database),
        privacy, model_is_local=model_is_local,
    )


def _source(database: Path, path: Path, text: str) -> Source:
    source = SourceRepository(database).add(Source(None, path, "a" * 64, SourceType.MARKDOWN, 1, NOW, NOW, NOW))
    SourceFragmentRepository(database).replace_for_source(
        ExtractionResult(source.id or 0, (SourceFragment(None, source.id or 0, "Notes", 0, text, "lines 1-2"),))
    )
    return source


def test_read_only_tools_return_provenance_without_changing_domain_state(tmp_path: Path) -> None:
    database = tmp_path / "steward.db"
    initialize_database(database)
    source = _source(database, tmp_path / "tlb.md", "TLBs cache translations.")
    activity = ActivityService(database)
    activity.record(
        ActivityType.SOURCE_CAPTURED,
        object_id=str(source.id),
        details=str(tmp_path / "private" / "tlb.md"),
    )
    service = _service(database)

    source_hits = json.loads(service.search_sources("translations"))
    read = json.loads(service.read_source(source.id or 0))
    events = json.loads(service.search_activity("captured"))

    assert source_hits[0]["filename"] == "tlb.md"
    assert "path" not in source_hits[0]
    assert read["filename"] == "tlb.md"
    assert "path" not in read
    assert read["fragments"][0]["text"] == "TLBs cache translations."
    assert events[0]["event_type"] == "source_captured"
    assert events[0]["details"] == "local file: tlb.md"
    assert str(tmp_path) not in json.dumps({"hits": source_hits, "read": read, "events": events})
    assert len(SourceRepository(database).list_all()) == 1
    assert len(activity.list_recent()) == 1


def test_source_tools_expose_only_the_read_only_source_tool_set(tmp_path: Path) -> None:
    database = tmp_path / "steward.db"
    initialize_database(database)

    assert [tool.name for tool in build_source_read_only_tools(_service(database))] == [
        "search_sources", "read_source", "search_activity",
    ]


def test_read_only_tools_withhold_missing_sources(tmp_path: Path) -> None:
    database = tmp_path / "steward.db"
    initialize_database(database)
    source = _source(database, tmp_path / "tlb.md", "TLBs cache translations.")
    SourceRepository(database).update(replace(source, status=SourceStatus.MISSING))
    service = _service(database)

    assert "error" in json.loads(service.read_source(source.id or 0))
    assert service.search_sources("translations") == "[]"


def test_read_only_tools_do_not_return_private_source_text_to_cloud_agent(tmp_path: Path) -> None:
    database = tmp_path / "steward.db"
    initialize_database(database)
    source = _source(database, tmp_path / "private.md", "Do not disclose this.")
    privacy = PrivacyService(database)
    privacy.set_rule(source.id or 0, PrivacyRule.NO_MODEL)
    service = _service(database, privacy=privacy)

    assert json.loads(service.search_sources("disclose")) == []
    assert "privacy rule" in json.loads(service.read_source(source.id or 0))["error"]


def test_read_only_tools_allow_local_models_to_read_local_only_sources(tmp_path: Path) -> None:
    database = tmp_path / "steward.db"
    initialize_database(database)
    source = _source(database, tmp_path / "local.md", "Local-only OpenMP notes.")
    privacy = PrivacyService(database)
    privacy.set_rule(source.id or 0, PrivacyRule.LOCAL_MODEL_ONLY)

    cloud = _service(database, privacy=privacy)
    local = _service(database, privacy=privacy, model_is_local=True)

    assert json.loads(cloud.search_sources("OpenMP")) == []
    assert json.loads(local.search_sources("OpenMP"))[0]["source_id"] == source.id
    assert "Local-only OpenMP notes." in json.loads(local.read_source(source.id or 0))["fragments"][0]["text"]


def test_read_only_tools_recover_when_an_agent_searches_a_markdown_filename(tmp_path: Path) -> None:
    database = tmp_path / "steward.db"
    initialize_database(database)
    source = _source(database, tmp_path / "COURSE_DETAILS.md", "Course details for CS3210.")

    results = json.loads(_service(database).search_sources("COURSE_DETAILS.md"))

    assert results[0]["source_id"] == source.id


def test_read_only_tool_limits_bound_invalid_model_arguments(tmp_path: Path) -> None:
    """Model-generated result limits cannot terminate an otherwise safe search."""
    database = tmp_path / "steward.db"
    initialize_database(database)
    service = _service(database)

    assert service._limit(0) == 1
    assert service._limit(21) == 20
