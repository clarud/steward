from pathlib import Path
from datetime import UTC, datetime

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
from steward.storage import initialize_database


def test_cli_without_a_command_shows_help(capsys) -> None:
    main([])

    assert "usage: steward" in capsys.readouterr().out


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
