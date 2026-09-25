"""The bot's background pass: rescan folders, recognise filed uploads, tell the uploader."""

import asyncio
from datetime import UTC, datetime
from pathlib import Path

from steward.capture import InboxCaptureService
from steward.cli.bootstrap import filed_notices, rescan_all
from steward.config import Settings
from steward.events import IncomingEvent
from steward.roots import SourceRootRepository
from steward.sources import SourceRepository
from steward.storage import initialize_database
from steward.telegram.adapter import run_periodically


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        data_dir=tmp_path / "data", inbox_dir=tmp_path / "inbox", log_level="INFO", model_provider="local",
        openai_model=None, soclaas_model=None, soclaas_base_url=None, gemini_model=None,
        local_model="test", local_model_url="http://127.0.0.1:11434",
    )


def test_rescan_recognises_a_filed_upload_and_tells_the_chat_that_sent_it(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    database = settings.data_dir / "steward.db"
    initialize_database(database)
    course = tmp_path / "Y4S1"; (course / "CS3210").mkdir(parents=True)
    SourceRootRepository(database).add("Y4S1", course)
    upload = tmp_path / "download.pdf"; upload.write_bytes(b"tutorial five")
    saved = InboxCaptureService(settings.inbox_dir, SourceRepository(database)).capture_file(
        IncomingEvent("telegram:9", "telegram", "100", "9", None, datetime.now(UTC), None, ("tut05.pdf",)), upload,
    )
    assert rescan_all(settings) == ()

    saved.source.path.rename(course / "CS3210" / "tut05.pdf")  # Codex files it
    moves = rescan_all(settings)

    assert [move.source.id for move in moves] == [saved.source.id]
    assert filed_notices(settings, moves) == [("100", "📁 tut05.pdf is now filed in Y4S1 / CS3210.")]
    assert "Nothing is waiting." in (settings.inbox_dir / "INBOX.md").read_text(encoding="utf-8")


def test_periodic_job_sends_notices_only_to_allowed_chats_and_survives_failures() -> None:
    sent: list[tuple[str, str]] = []
    calls = {"count": 0}

    class Bot:
        async def send_message(self, *, chat_id: str, text: str) -> None:
            sent.append((chat_id, text))

    def job():
        calls["count"] += 1
        if calls["count"] == 1:
            raise OSError("database busy")
        return [("100", "filed"), ("999", "not yours")]

    async def scenario() -> None:
        task = asyncio.create_task(run_periodically(Bot(), job, 0.01, frozenset({"100"})))
        while calls["count"] < 2 or not sent:
            await asyncio.sleep(0.01)
        task.cancel()

    asyncio.run(scenario())

    assert sent[0] == ("100", "filed")
    assert ("999", "not yours") not in sent
