from pathlib import Path

import pytest

from steward.config import Settings


def test_settings_use_safe_local_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("STEWARD_DATA_DIR", raising=False)
    monkeypatch.delenv("STEWARD_LOG_LEVEL", raising=False)

    assert Settings.from_environment() == Settings(
        data_dir=Path(".steward"), log_level="INFO"
    )


def test_settings_reject_invalid_log_level(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STEWARD_LOG_LEVEL", "VERBOSE")

    with pytest.raises(ValueError, match="STEWARD_LOG_LEVEL"):
        Settings.from_environment()

