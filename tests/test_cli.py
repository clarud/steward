from pathlib import Path
import sqlite3

import pytest

from steward.cli import build_parser, main
from steward.cli.commands import _configure_console_encoding
from steward.sources import SourceRepository
from steward.storage import initialize_database, snapshot_database
from steward.roots import SourceRootRepository




def test_cli_without_a_command_shows_help(capsys) -> None:
    main([])

    assert "usage: steward" in capsys.readouterr().out


def test_parser_exposes_only_live_commands() -> None:
    parser = build_parser()
    subparsers = next(action for action in parser._actions if getattr(action, "choices", None) is not None)

    assert set(subparsers.choices) == {
        "onboard-root", "scan-root", "roots", "relocate-root", "remove-root", "search", "ask", "inbox",
        "reextract", "unregister-source", "download-embedding-model",
        "rebuild-semantic-index", "evaluate-retrieval", "activity", "telegram", "health", "backup", "restore",
    }


def test_cli_backup_creates_local_snapshots_without_overwriting(tmp_path: Path, monkeypatch, capsys) -> None:
    data_dir = tmp_path / "data"; monkeypatch.setenv("STEWARD_DATA_DIR", str(data_dir))
    initialize_database(data_dir / "steward.db")
    destination = tmp_path / "backup"

    main(["backup", "--destination", str(destination)])

    assert "Backed up local Steward databases:" in capsys.readouterr().out
    assert (destination / "steward.db").is_file()
    main(["backup", "--destination", str(destination)])
    assert "already exists" in capsys.readouterr().out


def test_cli_backup_reports_a_failed_snapshot(tmp_path: Path, monkeypatch, capsys) -> None:
    data_dir = tmp_path / "data"
    monkeypatch.setenv("STEWARD_DATA_DIR", str(data_dir))
    data_dir.mkdir()
    (data_dir / "steward.db").write_bytes(b"not a database")
    destination = tmp_path / "partial"

    main(["backup", "--destination", str(destination)])

    output = capsys.readouterr().out
    assert "Backup failed" in output and "Backup set is incomplete" in output
    assert "Backed up local Steward databases:" not in output
    assert (data_dir / "steward.db").read_bytes() == b"not a database"


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




def test_cli_health_is_read_only_and_reports_database_root_and_telegram_state(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    data_dir = tmp_path / "data"; monkeypatch.setenv("STEWARD_DATA_DIR", str(data_dir))
    monkeypatch.setattr("steward.cli.commands.load_environment_file", lambda: None)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)

    main(["health"])

    assert capsys.readouterr().out == (
        "Steward health:\n"
        "Operational database: not initialized\n"
        "Authorized roots: not initialized\n"
        "Telegram token: not configured\n"
    )
    assert not (data_dir / "steward.db").exists()

    database = data_dir / "steward.db"
    initialize_database(database)
    available = tmp_path / "available"; available.mkdir()
    missing = tmp_path / "missing"; missing.mkdir()
    roots = SourceRootRepository(database)
    roots.add("Available", available); roots.add("Missing", missing)
    missing.rmdir()
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "private-token")

    main(["health"])

    assert capsys.readouterr().out == (
        "Steward health:\n"
        "Operational database: available\n"
        "Authorized roots: 1 available, 1 missing\n"
        "Telegram token: configured\n"
    )


def test_strict_health_exit_contract(tmp_path, monkeypatch, capsys):
    import pytest

    monkeypatch.setattr("steward.cli.commands.load_environment_file", lambda: None)
    monkeypatch.setenv("STEWARD_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    with pytest.raises(SystemExit) as failure:
        main(["health", "--strict"])
    assert failure.value.code == 1
    assert not (tmp_path / "steward.db").exists()
    initialize_database(tmp_path / "steward.db")
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
    roots.remove("Notes")
    main(["health", "--strict"])
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "   ")
    with pytest.raises(SystemExit):
        main(["health", "--strict"])


def test_cli_scan_and_root_scan_report_a_busy_database_without_touching_originals(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    vault = tmp_path / "vault"; vault.mkdir()
    note = vault / "note.md"; note.write_text("# Note", encoding="utf-8")
    data_dir = tmp_path / "data"; monkeypatch.setenv("STEWARD_DATA_DIR", str(data_dir))

    main(["onboard-root", "School", str(vault)])
    capsys.readouterr()

    def database_is_locked(_: Path) -> None:
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr("steward.cli.commands.initialize_database", database_is_locked)

    main(["scan-root", "School"])
    root_output = capsys.readouterr().out

    assert root_output == (
        "Root scan stopped: the local Steward database is busy. Wait for the other local Steward operation to finish, "
        "then retry. Original files were not changed.\n"
    )
    assert note.read_text(encoding="utf-8") == "# Note"


def test_cli_relocate_root_requires_confirmation_and_preserves_source_identity(tmp_path, monkeypatch, capsys):
    data = tmp_path / "data"; old = tmp_path / "old"; old.mkdir()
    note = old / "note.md"; note.write_text("# Original", encoding="utf-8")
    monkeypatch.setenv("STEWARD_DATA_DIR", str(data))
    monkeypatch.setattr("steward.cli.commands.load_environment_file", lambda: None)
    main(["onboard-root", "School", str(old)]); capsys.readouterr()
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

    main(["onboard-root", "School", str(vault), "--exclude", "generated"])
    capsys.readouterr()
    main(["scan-root", "School"])

    assert capsys.readouterr().out == "Scan complete for School: new=0 updated=0 unchanged=1 missing=0\n"
    sources = SourceRepository(data_dir / "steward.db")
    assert sources.get_by_path((vault / "note.md").resolve()) is not None
    assert sources.get_by_path((generated / "output.md").resolve()) is None


def test_cli_onboard_root_authorizes_and_scans_an_existing_directory_in_place(tmp_path: Path, monkeypatch, capsys) -> None:
    vault = tmp_path / "existing-notes"; vault.mkdir()
    note = vault / "note.md"; note.write_text("# Existing note", encoding="utf-8")
    generated = vault / "generated"; generated.mkdir()
    (generated / "output.md").write_text("# Generated", encoding="utf-8")
    data_dir = tmp_path / "data"; monkeypatch.setenv("STEWARD_DATA_DIR", str(data_dir))

    main(["onboard-root", "Existing Notes", str(vault), "--exclude", "generated"])

    output = capsys.readouterr().out
    assert "Authorized source root 'Existing Notes' and scanned it in place: new=1" in output
    assert "Original files were not moved, copied, or rewritten." in output
    root = SourceRootRepository(data_dir / "steward.db").get_by_name("Existing Notes")
    assert root is not None and root.path == vault.resolve() and root.exclusions == (Path("generated"),)
    sources = SourceRepository(data_dir / "steward.db")
    assert sources.get_by_path(note.resolve()) is not None
    assert sources.get_by_path((generated / "output.md").resolve()) is None

    main(["onboard-root", "Existing Notes", str(vault), "--exclude", "generated"])

    assert "Existing authorization reused" in capsys.readouterr().out




def test_cli_reextract_reports_a_missing_source_without_loading_a_model(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("STEWARD_DATA_DIR", str(tmp_path / "data"))

    main(["reextract", "99"])

    assert capsys.readouterr().out == "Source 99 was not found.\n"




def test_cli_unregister_source_requires_confirmation_and_retains_original(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    note_path = vault / "note.md"
    note_path.write_text("# Note", encoding="utf-8")
    data_dir = tmp_path / "data"
    monkeypatch.setenv("STEWARD_DATA_DIR", str(data_dir))
    main(["onboard-root", "Vault", str(vault)])
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
    main(["onboard-root", "Vault", str(vault)])
    capsys.readouterr()

    main(["search", "translations", "--mode", "keyword"])

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
    main(["onboard-root", "Vault", str(vault)])
    capsys.readouterr()

    main(["search", "translations", "--mode", "keyword", "--type", "plain_text"])

    output = capsys.readouterr().out
    assert str(plain_text.resolve()) in output
    assert str(markdown.resolve()) not in output


def test_cli_evaluates_retrieval_cases_against_an_indexed_vault(tmp_path: Path, monkeypatch, capsys) -> None:
    vault = tmp_path / "vault"; vault.mkdir()
    (vault / "network.md").write_text("# Queueing\nPackets wait in queues.", encoding="utf-8")
    cases = tmp_path / "cases.yaml"
    cases.write_text(
        "cases:\n  - query: queueing\n    file: network.md\n  - query: zebra\n    file: network.md\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("STEWARD_DATA_DIR", str(tmp_path / "data"))
    main(["onboard-root", "Vault", str(vault)])
    capsys.readouterr()

    main(["evaluate-retrieval", str(cases)])

    assert capsys.readouterr().out == (
        "Mode: keyword\nCases: 2\nHit@1: 50%\nHit@3: 50%\nMRR: 0.500\n"
        "Not in the top 3:\n- zebra -> network.md\n"
    )


def test_cli_ask_explains_required_gemini_configuration(monkeypatch, capsys) -> None:
    monkeypatch.setattr("steward.cli.commands.load_environment_file", lambda: None)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("STEWARD_GEMINI_MODEL", raising=False)
    monkeypatch.delenv("STEWARD_MODEL_PROVIDER", raising=False)

    main(["ask", "What do I know about TLBs?"])

    assert capsys.readouterr().out == (
        "Set GEMINI_API_KEY and STEWARD_GEMINI_MODEL before using `steward ask`.\n"
    )


def test_cli_ask_runs_the_ask_flow_and_prints_sources(tmp_path: Path, monkeypatch, capsys) -> None:
    from steward.graphs.ask import AskResult

    seen = {}

    def fake_run_ask(graph, question, **_kwargs):
        seen["question"] = question
        return AskResult("answered", "Grounded answer.", (), removed=1)

    monkeypatch.setenv("STEWARD_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr("steward.cli.commands.load_environment_file", lambda: None)
    monkeypatch.setattr("steward.cli.commands.model_gateway_from_settings", lambda *_args, **_kwargs: object())
    monkeypatch.setattr("steward.cli.commands.optional_embedding_provider", lambda: None)
    monkeypatch.setattr("steward.cli.commands.run_ask", fake_run_ask)

    main(["ask", "What is MM1?"])

    assert seen == {"question": "What is MM1?"}
    assert capsys.readouterr().out == "Grounded answer.\n(1 statement(s) removed: not supported by your files.)\n"








def test_cli_telegram_explains_required_bot_token(monkeypatch, capsys) -> None:
    monkeypatch.setattr("steward.cli.commands.load_environment_file", lambda: None)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)

    main(["telegram"])

    assert capsys.readouterr().out == (
        "Set TELEGRAM_BOT_TOKEN before using `steward telegram`.\n"
    )
























def test_cli_configures_a_non_utf8_console_for_utf8(monkeypatch) -> None:
    class Console:
        def __init__(self) -> None:
            self.encoding = None

        def reconfigure(self, *, encoding: str) -> None:
            self.encoding = encoding

    console = Console()
    monkeypatch.setattr("steward.cli.commands.sys.stdout", console)

    _configure_console_encoding()

    assert console.encoding == "utf-8"


def test_cli_explains_a_missing_optional_extra(monkeypatch, capsys) -> None:
    from steward.extras import MissingExtraError

    def needs_google(*_args, **_kwargs):
        raise MissingExtraError("google", "Google Drive import")

    monkeypatch.setattr("steward.cli.commands.load_environment_file", lambda: None)
    monkeypatch.setattr("steward.cli.commands.health_report", needs_google)

    with pytest.raises(SystemExit) as exit_info:
        main(["health"])

    assert exit_info.value.code == 1
    assert 'pip install "steward[google]"' in capsys.readouterr().out


def test_cli_remove_root_requires_confirmation_and_leaves_files(tmp_path: Path, monkeypatch, capsys) -> None:
    vault = tmp_path / "vault"; vault.mkdir()
    note = vault / "note.md"; note.write_text("# Note", encoding="utf-8")
    data_dir = tmp_path / "data"; monkeypatch.setenv("STEWARD_DATA_DIR", str(data_dir))
    monkeypatch.setattr("steward.cli.commands.load_environment_file", lambda: None)
    main(["onboard-root", "School", str(vault)]); capsys.readouterr()

    main(["remove-root", "School"])
    assert "Re-run with --confirm" in capsys.readouterr().out
    main(["remove-root", "School", "--confirm"])

    assert "forgot 1 file(s)" in capsys.readouterr().out
    assert SourceRepository(data_dir / "steward.db").list_all() == []
    assert note.read_text(encoding="utf-8") == "# Note"
