from steward.cli import main


def test_cli_starts_without_side_effects(capsys) -> None:
    main()

    assert capsys.readouterr().out == "Steward foundation initialized.\n"

