from pathlib import Path

import pytest

from steward.config import Settings, load_environment_file


def test_settings_use_safe_local_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("STEWARD_DATA_DIR", raising=False)
    monkeypatch.delenv("STEWARD_LOG_LEVEL", raising=False)
    monkeypatch.delenv("STEWARD_MODEL_PROVIDER", raising=False)
    monkeypatch.delenv("STEWARD_OPENAI_MODEL", raising=False)
    monkeypatch.delenv("STEWARD_GEMINI_MODEL", raising=False)

    assert Settings.from_environment() == Settings(
        data_dir=Path(".steward"),
        inbox_dir=Path("vault/inbox"),
        log_level="INFO",
        model_provider="gemini",
        openai_model=None,
        gemini_model=None,
    )


def test_settings_reject_invalid_log_level(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STEWARD_LOG_LEVEL", "VERBOSE")

    with pytest.raises(ValueError, match="STEWARD_LOG_LEVEL"):
        Settings.from_environment()


def test_settings_reject_invalid_model_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STEWARD_MODEL_PROVIDER", "local-magic")

    with pytest.raises(ValueError, match="STEWARD_MODEL_PROVIDER"):
        Settings.from_environment()


def test_environment_file_does_not_override_explicit_shell_values(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / ".env").write_text("STEWARD_LOG_LEVEL=DEBUG\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("STEWARD_LOG_LEVEL", "ERROR")

    load_environment_file()

    assert Settings.from_environment().log_level == "ERROR"
