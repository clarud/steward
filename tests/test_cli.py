from pathlib import Path

from steward.cli import main
from steward.sources import SourceRepository


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
