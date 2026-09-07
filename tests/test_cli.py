from pathlib import Path
from datetime import UTC, datetime

from steward.cli import main
from steward.extraction import SourceFragmentRepository
from steward.sources import Source, SourceRepository, SourceType
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
    assert "A TLB caches address translations." in output


def test_cli_ask_explains_required_gemini_configuration(monkeypatch, capsys) -> None:
    monkeypatch.setattr("steward.cli.load_environment_file", lambda: None)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("STEWARD_GEMINI_MODEL", raising=False)
    monkeypatch.delenv("STEWARD_MODEL_PROVIDER", raising=False)

    main(["ask", "What do I know about TLBs?"])

    assert capsys.readouterr().out == (
        "Set GEMINI_API_KEY and STEWARD_GEMINI_MODEL before using `steward ask`.\n"
    )


def test_cli_agent_explains_required_gemini_configuration(monkeypatch, capsys) -> None:
    monkeypatch.setattr("steward.cli.load_environment_file", lambda: None)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("STEWARD_GEMINI_MODEL", raising=False)
    monkeypatch.delenv("STEWARD_MODEL_PROVIDER", raising=False)

    main(["agent", "What do I know about TLBs?"])

    assert capsys.readouterr().out == (
        "Set GEMINI_API_KEY and STEWARD_GEMINI_MODEL before using `steward agent`.\n"
    )


def test_cli_telegram_explains_required_bot_token(monkeypatch, capsys) -> None:
    monkeypatch.setattr("steward.cli.load_environment_file", lambda: None)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)

    main(["telegram"])

    assert capsys.readouterr().out == (
        "Set TELEGRAM_BOT_TOKEN before using `steward telegram`.\n"
    )


def test_cli_calendar_search_explains_oauth_client_setup(monkeypatch, capsys) -> None:
    monkeypatch.setattr("steward.cli.load_environment_file", lambda: None)
    monkeypatch.delenv("STEWARD_GOOGLE_CLIENT_SECRETS", raising=False)

    main(["calendar-search", "Tokyo"])

    assert capsys.readouterr().out == (
        "Set STEWARD_GOOGLE_CLIENT_SECRETS or pass --client-secrets before reading Calendar.\n"
    )


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
