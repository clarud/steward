from pathlib import Path
from datetime import UTC, datetime, timedelta
import sqlite3

from steward.cli import (
    _configure_console_encoding,
    _is_calendar_question,
    _is_calendar_write_request,
    _tool_calling_model_from_settings,
    build_parser,
    main,
)
from steward.config import Settings
from steward.graphs import OllamaToolCallingModel, OpenAICompatibleToolCallingModel
from steward.extraction import SourceFragmentRepository
from steward.sources import Source, SourceRepository, SourceType
from steward.records import RecordService, TravelRecord
from steward.knowledge import KnowledgeService
from steward.extraction import ExtractionResult, SourceFragment
from steward.storage import initialize_database, snapshot_database
from steward.activity import ActivityService, ActivityType
from steward.roots import SourceRootRepository
from steward.telegram import TelegramUpdateDeliveryRepository


def test_cli_without_a_command_shows_help(capsys) -> None:
    main([])

    assert "usage: steward" in capsys.readouterr().out


def test_cli_backup_creates_local_snapshots_without_overwriting(tmp_path: Path, monkeypatch, capsys) -> None:
    data_dir = tmp_path / "data"; monkeypatch.setenv("STEWARD_DATA_DIR", str(data_dir))
    initialize_database(data_dir / "steward.db")
    initialize_database(data_dir / "checkpoints.db")
    destination = tmp_path / "backup"

    main(["backup", "--destination", str(destination)])

    output = capsys.readouterr().out
    assert "Backed up local Steward databases:" in output
    assert "copied sequentially" in output
    assert (destination / "steward.db").is_file()
    assert (destination / "checkpoints.db").is_file()
    main(["backup", "--destination", str(destination)])
    assert "already exists" in capsys.readouterr().out


def test_cli_backup_reports_and_preserves_partial_set(tmp_path: Path, monkeypatch, capsys) -> None:
    data_dir = tmp_path / "data"
    monkeypatch.setenv("STEWARD_DATA_DIR", str(data_dir))
    initialize_database(data_dir / "steward.db")
    (data_dir / "checkpoints.db").write_bytes(b"corrupt checkpoint")
    destination = tmp_path / "partial"
    main(["backup", "--destination", str(destination)])
    output = capsys.readouterr().out
    assert "Backup set is incomplete" in output
    assert "Completed snapshots retained" in output
    assert "Retry with a new destination" in output
    assert "Backed up local Steward databases:" not in output
    assert (destination / "steward.db").is_file()
    assert not (destination / "checkpoints.db").exists()
    with sqlite3.connect(destination / "steward.db") as connection:
        assert connection.execute("PRAGMA quick_check").fetchone() == ("ok",)
    assert (data_dir / "checkpoints.db").read_bytes() == b"corrupt checkpoint"


def test_cli_restore_requires_confirmation_then_preserves_a_safety_backup(tmp_path: Path, monkeypatch, capsys) -> None:
    data_dir = tmp_path / "data"; monkeypatch.setenv("STEWARD_DATA_DIR", str(data_dir))
    active = data_dir / "steward.db"; initialize_database(active)
    with sqlite3.connect(active) as connection:
        connection.execute("INSERT INTO activity_events (event_type, object_id, details, occurred_at) VALUES (?, ?, ?, ?)", ("source_captured", "1", "before", "2026-09-10T00:00:00+00:00"))
    snapshot = snapshot_database(active, tmp_path / "snapshot.db")
    with sqlite3.connect(active) as connection:
        connection.execute("INSERT INTO activity_events (event_type, object_id, details, occurred_at) VALUES (?, ?, ?, ?)", ("source_captured", "2", "after", "2026-09-10T00:01:00+00:00"))
    safety = tmp_path / "safety.db"

    main(["restore", "--snapshot", str(snapshot), "--destination", str(active), "--safety-backup", str(safety)])
    assert capsys.readouterr().out.startswith("Refusing to restore without --confirm.")
    main(["restore", "--snapshot", str(snapshot), "--destination", str(active), "--safety-backup", str(safety), "--confirm"])

    assert "Restored" in capsys.readouterr().out
    with sqlite3.connect(active) as connection:
        assert connection.execute("SELECT details FROM activity_events ORDER BY id").fetchall() == [("before",)]
    assert safety.is_file()


def test_cli_scan_registers_markdown_sources(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    note_path = vault / "note.md"
    note_path.write_text("# Note")
    data_dir = tmp_path / "data"
    monkeypatch.setenv("STEWARD_DATA_DIR", str(data_dir))

    main(["scan", str(vault)])

    assert capsys.readouterr().out == (
        "Scan complete: new=1 updated=0 unchanged=0 missing=0\n"
    )
    source = SourceRepository(data_dir / "steward.db").get_by_path(note_path.resolve())
    assert source is not None
    fragments = SourceFragmentRepository(data_dir / "steward.db").list_for_source(
        source.id or 0
    )
    assert [fragment.heading for fragment in fragments] == ["Note"]


def test_cli_health_is_read_only_and_reports_database_root_and_telegram_state(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    data_dir = tmp_path / "data"; monkeypatch.setenv("STEWARD_DATA_DIR", str(data_dir))
    monkeypatch.setattr("steward.cli.load_environment_file", lambda: None)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)

    main(["health"])

    assert capsys.readouterr().out == (
        "Steward health:\n"
        "Operational database: not initialized\n"
        "Conversation checkpoints: not initialized\n"
        "Authorized roots: not initialized\n"
        "Telegram token: not configured\n"
    )
    assert not (data_dir / "steward.db").exists()

    database = data_dir / "steward.db"; checkpoints = data_dir / "checkpoints.db"
    initialize_database(database)
    from langgraph.checkpoint.sqlite import SqliteSaver
    with SqliteSaver.from_conn_string(str(checkpoints)) as saver:
        saver.setup()
    available = tmp_path / "available"; available.mkdir()
    missing = tmp_path / "missing"; missing.mkdir()
    disabled = tmp_path / "disabled"; disabled.mkdir()
    roots = SourceRootRepository(database)
    roots.add("Available", available); roots.add("Missing", missing); roots.add("Disabled", disabled)
    missing.rmdir(); roots.set_enabled("Disabled", False)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "private-token")

    main(["health"])

    assert capsys.readouterr().out == (
        "Steward health:\n"
        "Operational database: available\n"
        "Conversation checkpoints: available\n"
        "Authorized roots: 1 available, 1 missing, 1 disabled\n"
        "Telegram token: configured\n"
    )


def test_strict_health_exit_contract_and_database_roles(tmp_path, monkeypatch, capsys):
    import pytest
    from langgraph.checkpoint.sqlite import SqliteSaver
    from steward.cli import _database_health

    monkeypatch.setattr("steward.cli.load_environment_file", lambda: None)
    monkeypatch.setenv("STEWARD_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    with pytest.raises(SystemExit) as failure:
        main(["health", "--strict"])
    assert failure.value.code == 1
    assert not (tmp_path / "steward.db").exists()
    initialize_database(tmp_path / "steward.db")
    with SqliteSaver.from_conn_string(str(tmp_path / "checkpoints.db")) as saver:
        saver.setup()
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "synthetic-token")
    # No registered roots is valid for an Inbox-only installation.
    main(["health", "--strict"])
    assert "synthetic-token" not in capsys.readouterr().out
    root = tmp_path / "notes"
    root.mkdir()
    roots = SourceRootRepository(tmp_path / "steward.db")
    roots.add("Notes", root)
    root.rmdir()
    with pytest.raises(SystemExit):
        main(["health", "--strict"])
    roots.set_enabled("Notes", False)
    main(["health", "--strict"])
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "   ")
    with pytest.raises(SystemExit):
        main(["health", "--strict"])
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "synthetic-token")
    checkpoint = tmp_path / "checkpoints.db"
    original = checkpoint.read_bytes()
    checkpoint.write_bytes(b"not a SQLite database")
    assert _database_health(checkpoint) == "unavailable"
    with pytest.raises(SystemExit):
        main(["health", "--strict"])
    assert checkpoint.read_bytes() == b"not a SQLite database"
    checkpoint.write_bytes((tmp_path / "steward.db").read_bytes())
    with pytest.raises(SystemExit):
        main(["health", "--strict"])
    checkpoint.write_bytes(original)
    main(["health", "--strict"])


def test_cli_scan_and_root_scan_report_a_busy_database_without_touching_originals(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    vault = tmp_path / "vault"; vault.mkdir()
    note = vault / "note.md"; note.write_text("# Note", encoding="utf-8")
    data_dir = tmp_path / "data"; monkeypatch.setenv("STEWARD_DATA_DIR", str(data_dir))

    main(["add-root", "School", str(vault)])
    capsys.readouterr()

    def database_is_locked(_: Path) -> None:
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr("steward.cli.initialize_database", database_is_locked)

    main(["scan", str(vault)])
    scan_output = capsys.readouterr().out
    main(["scan-root", "School"])
    root_output = capsys.readouterr().out

    assert scan_output == (
        "Scan stopped: the local Steward database is busy. Wait for the other local Steward operation to finish, "
        "then retry. Original files were not changed.\n"
    )
    assert root_output == (
        "Root scan stopped: the local Steward database is busy. Wait for the other local Steward operation to finish, "
        "then retry. Original files were not changed.\n"
    )
    assert note.read_text(encoding="utf-8") == "# Note"


def test_cli_relocate_root_requires_confirmation_and_preserves_source_identity(tmp_path, monkeypatch, capsys):
    data = tmp_path / "data"; old = tmp_path / "old"; old.mkdir()
    note = old / "note.md"; note.write_text("# Original", encoding="utf-8")
    monkeypatch.setenv("STEWARD_DATA_DIR", str(data))
    monkeypatch.setattr("steward.cli.load_environment_file", lambda: None)
    main(["add-root", "School", str(old)]); capsys.readouterr()
    main(["scan-root", "School"]); capsys.readouterr()
    sources = SourceRepository(data / "steward.db")
    source = sources.get_by_path(note.resolve())
    new = tmp_path / "new"; old.rename(new)

    main(["relocate-root", "School", str(new)])
    assert "without --confirm" in capsys.readouterr().out
    assert SourceRootRepository(data / "steward.db").get_by_name("School").path == old.resolve()
    main(["relocate-root", "School", str(new), "--confirm"])
    output = capsys.readouterr().out

    assert "verified and updated 1 tracked source paths" in output
    relocated = sources.get_by_id(source.id)
    assert relocated.id == source.id
    assert relocated.path == (new / "note.md").resolve()
    assert relocated.content_hash == source.content_hash
    main(["scan-root", "School"])
    assert "new=0" in capsys.readouterr().out


def test_cli_scan_root_uses_the_locally_authorized_exclusions(tmp_path: Path, monkeypatch, capsys) -> None:
    vault = tmp_path / "vault"; vault.mkdir()
    (vault / "note.md").write_text("# Note", encoding="utf-8")
    generated = vault / "generated"; generated.mkdir()
    (generated / "output.md").write_text("# Output", encoding="utf-8")
    data_dir = tmp_path / "data"
    monkeypatch.setenv("STEWARD_DATA_DIR", str(data_dir))

    main(["add-root", "School", str(vault), "--exclude", "generated"])
    capsys.readouterr()
    main(["scan-root", "School"])

    assert capsys.readouterr().out == "Scan complete for School: new=1 updated=0 unchanged=0 missing=0\n"
    sources = SourceRepository(data_dir / "steward.db")
    assert sources.get_by_path((vault / "note.md").resolve()) is not None
    assert sources.get_by_path((generated / "output.md").resolve()) is None


def test_cli_can_disable_a_root_before_scan(tmp_path: Path, monkeypatch, capsys) -> None:
    vault = tmp_path / "vault"; vault.mkdir()
    data_dir = tmp_path / "data"; monkeypatch.setenv("STEWARD_DATA_DIR", str(data_dir))
    main(["add-root", "School", str(vault)]); capsys.readouterr()

    main(["disable-root", "School"])
    assert capsys.readouterr().out == "Source root 'School' is now disabled.\n"
    main(["scan-root", "School"])
    assert capsys.readouterr().out == "Source root 'School' is disabled.\n"


def test_cli_reextract_reports_a_missing_source_without_loading_a_model(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("STEWARD_DATA_DIR", str(tmp_path / "data"))

    main(["reextract", "99"])

    assert capsys.readouterr().out == "Source 99 was not found.\n"


def test_cli_sources_lists_registered_sources(tmp_path: Path, monkeypatch, capsys) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    note_path = vault / "note.md"
    note_path.write_text("# Note")
    data_dir = tmp_path / "data"
    monkeypatch.setenv("STEWARD_DATA_DIR", str(data_dir))
    main(["scan", str(vault)])
    capsys.readouterr()

    main(["sources"])

    assert capsys.readouterr().out == f"1\tactive\t{note_path.resolve()}\n"


def test_cli_unregister_source_requires_confirmation_and_retains_original(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    note_path = vault / "note.md"
    note_path.write_text("# Note", encoding="utf-8")
    data_dir = tmp_path / "data"
    monkeypatch.setenv("STEWARD_DATA_DIR", str(data_dir))
    main(["scan", str(vault)])
    capsys.readouterr()

    main(["unregister-source", "1"])

    assert "will be unregistered" in capsys.readouterr().out
    assert SourceRepository(data_dir / "steward.db").get_by_id(1) is not None

    main(["unregister-source", "1", "--confirm"])

    assert "original file was retained" in capsys.readouterr().out
    assert note_path.is_file()
    assert SourceRepository(data_dir / "steward.db").get_by_id(1) is None


def test_cli_search_returns_matching_fragment(tmp_path: Path, monkeypatch, capsys) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    note_path = vault / "note.md"
    note_path.write_text("# TLB\nA TLB caches address translations.")
    data_dir = tmp_path / "data"
    monkeypatch.setenv("STEWARD_DATA_DIR", str(data_dir))
    main(["scan", str(vault)])
    capsys.readouterr()

    main(["search", "translations"])

    output = capsys.readouterr().out
    assert f"{note_path.resolve()}:lines 1-2 [TLB]" in output
    assert "A TLB caches address [translations]." in output


def test_cli_search_filters_results_by_source_type(tmp_path: Path, monkeypatch, capsys) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    markdown = vault / "note.md"; markdown.write_text("# TLB\nAddress translations", encoding="utf-8")
    plain_text = vault / "note.txt"; plain_text.write_text("Address translations", encoding="utf-8")
    data_dir = tmp_path / "data"
    monkeypatch.setenv("STEWARD_DATA_DIR", str(data_dir))
    main(["scan", str(vault)])
    capsys.readouterr()

    main(["search", "translations", "--source-type", "plain_text"])

    output = capsys.readouterr().out
    assert str(plain_text.resolve()) in output
    assert str(markdown.resolve()) not in output


def test_cli_evaluates_retrieval_cases_against_an_indexed_vault(tmp_path: Path, monkeypatch, capsys) -> None:
    vault = tmp_path / "vault"; vault.mkdir()
    (vault / "network.md").write_text("# Queueing\nPackets wait in queues.", encoding="utf-8")
    cases = tmp_path / "cases.yaml"
    cases.write_text(
        "cases:\n  - query: queueing\n    expected:\n      source: network.md\n      heading: Queueing\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("STEWARD_DATA_DIR", str(tmp_path / "data"))
    main(["scan", str(vault)])
    capsys.readouterr()

    main(["evaluate-retrieval", str(vault), str(cases)])

    assert capsys.readouterr().out == "Mode: lexical\nCases: 1\nRecall@5: 100.0%\nMRR: 1.000\n"


def test_cli_ask_explains_required_gemini_configuration(monkeypatch, capsys) -> None:
    monkeypatch.setattr("steward.cli.load_environment_file", lambda: None)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("STEWARD_GEMINI_MODEL", raising=False)
    monkeypatch.delenv("STEWARD_MODEL_PROVIDER", raising=False)

    main(["ask", "What do I know about TLBs?"])

    assert capsys.readouterr().out == (
        "Set GEMINI_API_KEY and STEWARD_GEMINI_MODEL before using `steward ask`.\n"
    )


def test_cli_ask_supplies_default_checkpointer_thread(monkeypatch, capsys) -> None:
    class FakeGraph:
        def __init__(self) -> None:
            self.input = None
            self.config = None

        def invoke(self, input, config):
            self.input = input
            self.config = config
            return {"answer": "Grounded answer.", "citations": ()}

    graph = FakeGraph()
    monkeypatch.setattr("steward.cli._model_gateway_from_settings", lambda *_args, **_kwargs: object())
    monkeypatch.setattr("steward.cli._build_question_graph", lambda *_args, **_kwargs: graph)

    main(["ask", "What is MM1?"])

    assert graph.input == {"question": "What is MM1?"}
    assert graph.config == {"configurable": {"thread_id": "cli:ask"}}
    assert capsys.readouterr().out == "Grounded answer.\n"
    assert build_parser().parse_args(["ask", "Question", "--thread-id", "review"]).thread_id == "review"


def test_cli_agent_explains_required_gemini_configuration(monkeypatch, capsys) -> None:
    monkeypatch.setattr("steward.cli.load_environment_file", lambda: None)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("STEWARD_GEMINI_MODEL", raising=False)
    monkeypatch.delenv("STEWARD_MODEL_PROVIDER", raising=False)

    main(["agent", "What do I know about TLBs?"])

    assert capsys.readouterr().out == (
        "Set GEMINI_API_KEY and STEWARD_GEMINI_MODEL before using `steward agent`.\n"
    )


def test_cli_selects_the_local_ollama_tool_adapter() -> None:
    adapter = _tool_calling_model_from_settings(
        Settings(
            data_dir=Path(".steward"),
            inbox_dir=Path("vault/inbox"),
            log_level="INFO",
            model_provider="local",
            openai_model=None,
            soclaas_model=None,
            soclaas_base_url=None,
            gemini_model=None,
            local_model="qwen3",
            local_model_url="http://127.0.0.1:11434",
        )
    )

    assert isinstance(adapter, OllamaToolCallingModel)


def test_cli_selects_soclaas_tool_adapter(monkeypatch) -> None:
    monkeypatch.setenv("STEWARD_SOCLAAS_API_KEY", "test-key")
    adapter = _tool_calling_model_from_settings(
        Settings(
            data_dir=Path(".steward"),
            inbox_dir=Path("vault/inbox"),
            log_level="INFO",
            model_provider="soclaas",
            openai_model=None,
            soclaas_model="llama3.1:8b",
            soclaas_base_url="https://gateway.example/v1",
            gemini_model=None,
            local_model=None,
            local_model_url="http://127.0.0.1:11434",
        )
    )

    assert isinstance(adapter, OpenAICompatibleToolCallingModel)


def test_cli_telegram_explains_required_bot_token(monkeypatch, capsys) -> None:
    monkeypatch.setattr("steward.cli.load_environment_file", lambda: None)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)

    main(["telegram"])

    assert capsys.readouterr().out == (
        "Set TELEGRAM_BOT_TOKEN before using `steward telegram`.\n"
    )


def test_cli_lists_empty_telegram_delivery_state(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("STEWARD_DATA_DIR", str(tmp_path / "data"))

    main(["telegram-deliveries"])

    assert capsys.readouterr().out == "No local Telegram delivery records.\n"


def test_cli_lists_empty_telegram_delivery_history(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("STEWARD_DATA_DIR", str(tmp_path / "data"))

    main(["telegram-delivery-history"])

    assert capsys.readouterr().out == "No local Telegram delivery history.\n"


def test_cli_lists_empty_telegram_dead_letters(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("STEWARD_DATA_DIR", str(tmp_path / ".steward"))
    main(["telegram-dead-letters"])
    assert capsys.readouterr().out == "No terminal Telegram delivery failures.\n"


def test_cli_requires_confirmation_before_reopening_telegram_dead_letter(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("STEWARD_DATA_DIR", str(tmp_path / ".steward"))

    main(["telegram-recover-dead-letter", "telegram:99"])

    assert capsys.readouterr().out.startswith("Refusing to reopen a Telegram dead letter without --confirm.")


def test_cli_reopens_dead_letter_with_an_audit_event(tmp_path: Path, monkeypatch, capsys) -> None:
    data_dir = tmp_path / ".steward"
    database_path = data_dir / "steward.db"
    initialize_database(database_path)
    deliveries = TelegramUpdateDeliveryRepository(database_path, max_attempts=1)
    now = datetime(2026, 9, 9, tzinfo=UTC)
    assert deliveries.claim("telegram:99", now=now)
    deliveries.release("telegram:99", now=now)
    assert deliveries.claim("telegram:99", now=now + timedelta(seconds=15)) is False
    monkeypatch.setenv("STEWARD_DATA_DIR", str(data_dir))

    main(["telegram-recover-dead-letter", "telegram:99", "--confirm"])

    assert capsys.readouterr().out == (
        "Reopened telegram:99 for a future genuine Telegram redelivery. No original Telegram message was replayed.\n"
    )
    assert ActivityService(database_path).list_recent()[0].event_type == ActivityType.TELEGRAM_DELIVERY_RECOVERED


def test_cli_calendar_search_explains_oauth_client_setup(monkeypatch, capsys) -> None:
    monkeypatch.setattr("steward.cli.load_environment_file", lambda: None)
    monkeypatch.delenv("STEWARD_GOOGLE_CLIENT_SECRETS", raising=False)

    main(["calendar-search", "Tokyo"])

    assert capsys.readouterr().out == (
        "Set STEWARD_GOOGLE_CLIENT_SECRETS or pass --client-secrets before reading Calendar.\n"
    )


def test_cli_creates_a_pending_calendar_proposal_without_contacting_google(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    data_dir = tmp_path / "data"; database = data_dir / "steward.db"; initialize_database(database)
    now = datetime(2026, 9, 8, tzinfo=UTC)
    source = SourceRepository(database).add(
        Source(None, tmp_path / "flight.pdf", "a" * 64, SourceType.PDF, 0, now, now, now)
    )
    record = RecordService(database).create_travel_record(
        TravelRecord(None, source.id or 0, "SQ638", "Singapore", "Tokyo", datetime(2026, 10, 1, 9, tzinfo=UTC), datetime(2026, 10, 1, 17, tzinfo=UTC), None)
    )
    monkeypatch.setenv("STEWARD_DATA_DIR", str(data_dir))

    main(["calendar-propose-travel-event", str(record.id)])

    assert capsys.readouterr().out == (
        "Calendar proposal 1 pending for travel record 1. "
        "Review with `steward calendar-review-travel-event 1 accepted`.\n"
    )


def test_cli_proposes_deterministic_knowledge_enrichment(tmp_path: Path, monkeypatch, capsys) -> None:
    data_dir = tmp_path / "data"; database = data_dir / "steward.db"; initialize_database(database)
    now = datetime(2026, 9, 8, tzinfo=UTC)
    source = SourceRepository(database).add(
        Source(None, tmp_path / "note.md", "a" * 64, SourceType.MARKDOWN, 0, now, now, now)
    )
    fragment = SourceFragmentRepository(database).replace_for_source(
        ExtractionResult(source.id or 0, (SourceFragment(None, source.id or 0, None, 0, "TLBs may cache translations.", "lines 1-1"),))
    )[0]
    knowledge = KnowledgeService(database)
    concept = knowledge.create_concept("TLB")
    claim = knowledge.create_claim(concept.id or 0, "TLBs cache translations.", [fragment.id or 0])
    monkeypatch.setenv("STEWARD_DATA_DIR", str(data_dir))

    main(["propose-knowledge-enrichment", str(claim.id), str(fragment.id)])

    assert capsys.readouterr().out.startswith("Knowledge enrichment proposal 1 pending: qualify\tclaim=1\tfragment=1\t")

    main(["knowledge-enrichment-proposals"])

    assert capsys.readouterr().out.startswith("1\tpending\tqualify\tclaim=1\tfragment=1\t")

    main(["review-knowledge-enrichment", "1", "accepted"])

    assert capsys.readouterr().out == "Knowledge enrichment proposal 1 accepted.\n"


def test_cli_creates_workspace(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("STEWARD_DATA_DIR", str(tmp_path / "data"))

    main(["create-workspace", "Steward"])

    assert capsys.readouterr().out == "Created workspace 1: Steward\n"


def test_cli_reviews_inbox_workspace_candidates(tmp_path: Path, monkeypatch, capsys) -> None:
    data_dir = tmp_path / "data"; database = data_dir / "steward.db"; initialize_database(database)
    now = datetime(2026, 9, 8, tzinfo=UTC)
    inbox = tmp_path / "vault" / "inbox"; inbox.mkdir(parents=True)
    repository = SourceRepository(database)
    for identifier, name in enumerate(("compiler-parsing.md", "compiler-notes.md"), start=1):
        path = inbox / name; path.write_text("note")
        repository.add(Source(None, path.resolve(), str(identifier) * 64, SourceType.MARKDOWN, 4, now, now, now))
    monkeypatch.setenv("STEWARD_DATA_DIR", str(data_dir))

    main(["review-inbox-workspaces"])

    assert "Compiler\tconfidence=0.70\tsources=1,2" in capsys.readouterr().out


def test_cli_reports_when_no_knowledge_connections_exist(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("STEWARD_DATA_DIR", str(tmp_path / "data"))

    main(["connect-knowledge"])

    assert capsys.readouterr().out == "No evidence-backed knowledge connections found.\n"


def test_cli_blocks_model_assisted_organization_for_private_cloud_source(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    data_dir = tmp_path / "data"; database = data_dir / "steward.db"; initialize_database(database)
    now = datetime(2026, 9, 8, tzinfo=UTC)
    source = SourceRepository(database).add(
        Source(None, tmp_path / "private.md", "a" * 64, SourceType.MARKDOWN, 0, now, now, now)
    )
    from steward.privacy import PrivacyService, PrivacyRule
    PrivacyService(database).set_rule(source.id or 0, PrivacyRule.LOCAL_MODEL_ONLY)
    monkeypatch.setenv("STEWARD_DATA_DIR", str(data_dir))
    monkeypatch.setenv("STEWARD_MODEL_PROVIDER", "gemini")
    monkeypatch.setattr(
        "steward.cli._model_gateway_from_settings",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not call model")),
    )

    main(["propose-organization", str(source.id), "--model-assisted"])

    assert capsys.readouterr().out == "This source's privacy policy does not permit the configured model.\n"


def test_cli_sets_and_reads_source_privacy(tmp_path: Path, monkeypatch, capsys) -> None:
    data_dir = tmp_path / "data"
    database = data_dir / "steward.db"
    initialize_database(database)
    now = datetime(2026, 9, 8, tzinfo=UTC)
    source = SourceRepository(database).add(
        Source(None, tmp_path / "private.md", "a" * 64, SourceType.MARKDOWN, 0, now, now, now)
    )
    monkeypatch.setenv("STEWARD_DATA_DIR", str(data_dir))

    main(["set-source-privacy", str(source.id), "no_model"])
    assert capsys.readouterr().out == "Source 1 privacy set to no_model.\n"
    main(["source-privacy", str(source.id)])
    assert capsys.readouterr().out == "no_model\n"


def test_calendar_question_routing_only_matches_unambiguous_schedule_requests() -> None:
    assert _is_calendar_question("What do I have coming up this week?") is True
    assert _is_calendar_question("Do I have anything scheduled tomorrow?") is True
    assert _is_calendar_question("Explain calendar queues in operating systems") is False
    assert _is_calendar_question("What is a queueing model?") is False
    assert _is_calendar_write_request("Put flight record 1 on my calendar") is True
    assert _is_calendar_write_request("What is on my calendar?") is False


def test_cli_configures_a_non_utf8_console_for_utf8(monkeypatch) -> None:
    class Console:
        def __init__(self) -> None:
            self.encoding = None

        def reconfigure(self, *, encoding: str) -> None:
            self.encoding = encoding

    console = Console()
    monkeypatch.setattr("steward.cli.sys.stdout", console)

    _configure_console_encoding()

    assert console.encoding == "utf-8"
