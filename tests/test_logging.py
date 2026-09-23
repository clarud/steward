import logging

from steward.config import Settings
from steward.logging import configure_logging


def test_configure_logging_writes_to_a_bounded_local_file(tmp_path) -> None:
    settings = Settings(
        data_dir=tmp_path / ".steward",
        inbox_dir=tmp_path / "vault" / "inbox",
        log_level="INFO",
        model_provider="local",
        openai_model=None,
        soclaas_model=None,
        soclaas_base_url=None,
        gemini_model=None,
        local_model="test",
        local_model_url="http://127.0.0.1:11434",
    )
    configure_logging(settings)
    logger = logging.getLogger("steward.test_logging")
    logger.info("safe test event")
    for handler in logging.getLogger().handlers:
        handler.flush()

    log_path = settings.data_dir / "logs" / "steward.log"
    assert "safe test event" in log_path.read_text(encoding="utf-8")
    handler = next(
        handler for handler in logging.getLogger().handlers
        if getattr(handler, "_steward_log_path", None) == log_path.resolve()
    )
    assert handler.maxBytes == 2 * 1024 * 1024
    assert handler.backupCount == 5


def test_telegram_bot_tokens_never_reach_the_log_file(tmp_path) -> None:
    settings = Settings(
        data_dir=tmp_path / ".steward",
        inbox_dir=tmp_path / "vault" / "inbox",
        log_level="INFO",
        model_provider="local",
        openai_model=None,
        soclaas_model=None,
        soclaas_base_url=None,
        gemini_model=None,
        local_model="test",
        local_model_url="http://127.0.0.1:11434",
    )
    configure_logging(settings)
    secret = "123456789:FAKEfakeFAKEfakeFAKEfake_0000"
    logging.getLogger("steward.test_logging").warning("POST https://api.telegram.org/bot%s/getUpdates", secret)
    logging.getLogger("httpx").info("HTTP Request: POST https://api.telegram.org/bot%s/getUpdates", secret)
    for handler in logging.getLogger().handlers:
        handler.flush()

    written = (settings.data_dir / "logs" / "steward.log").read_text(encoding="utf-8")
    assert secret not in written
    assert "api.telegram.org/bot<redacted>/getUpdates" in written
    assert logging.getLogger("httpx").getEffectiveLevel() == logging.WARNING
