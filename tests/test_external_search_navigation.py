"""Exercise Telegram search through configured services, without network access."""

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from steward import cli
from steward.capture import CaptureResult
from steward.application import StewardDriveImportApplication, StewardGmailImportApplication
from steward.events import IncomingEvent
from steward.presentation import PresentedReply
from steward.reviews import ReviewContextRepository
from steward.storage import initialize_database
from steward.telegram.callbacks import TelegramCallbackRepository


@pytest.mark.parametrize("provider", ["drive", "gmail"])
def test_duplicate_import_card_targets_existing_source_without_inbox_claim(provider, tmp_path):
    calls = []
    source = SimpleNamespace(id=87, path=tmp_path / "private-project" / "Already organized.txt")
    class Importer:
        def import_file(self, identifier):
            calls.append(identifier)
            return CaptureResult(source, True)

        import_message = import_file

    application_type = StewardDriveImportApplication if provider == "drive" else StewardGmailImportApplication
    event = IncomingEvent("duplicate", "telegram", "chat", "1", None, datetime.now(UTC), f"/{provider}_import remote-17")
    reply = application_type(Importer()).handle_command(event)
    assert calls == ["remote-17"]
    assert reply.title == "Already organized.txt"
    assert "existing source" in reply.text and "no new copy" in reply.text
    assert "Inbox" not in reply.text and "private-project" not in reply.text
    assert [action.command for action in reply.actions] == [
        "/source_content 87", "/source 87", "/source_workspaces 87",
    ]


@pytest.mark.parametrize("provider", ["drive", "gmail"])
@pytest.mark.parametrize("duplicate", [False, True])
def test_import_selects_retained_source_in_durable_chat_context(provider, duplicate, tmp_path):
    from dataclasses import replace
    database = tmp_path / "state.db"
    initialize_database(database)
    contexts = ReviewContextRepository(database)
    contexts.set("telegram", "other-chat", "source", 9)
    source = SimpleNamespace(id=87, path=tmp_path / "Imported.txt")
    class Importer:
        fail = False
        def import_file(self, identifier):
            if self.fail:
                raise RuntimeError("private provider diagnostic")
            return CaptureResult(source, duplicate)
        import_message = import_file
    importer = Importer()
    application_type = StewardDriveImportApplication if provider == "drive" else StewardGmailImportApplication
    event = IncomingEvent("import", "telegram", "chat", "1", None, datetime.now(UTC), f"/{provider}_import remote-17")
    application = application_type(importer, contexts=contexts)
    application.handle_command(event)
    restarted = ReviewContextRepository(database)
    assert restarted.get("telegram", "chat").identifier == 87
    assert restarted.get("telegram", "chat").kind == "source"
    assert restarted.get("telegram", "other-chat").identifier == 9
    importer.fail = True
    application.handle_command(replace(event, text=f"/{provider}_import unavailable"))
    assert restarted.get("telegram", "chat").identifier == 87

    class BrokenContexts:
        def set(self, *args):
            raise RuntimeError("secret SQLite path")
    importer.fail = False
    reply = application_type(importer, contexts=BrokenContexts()).handle_command(event)
    assert "Conversation selection could not be updated" in reply.text
    assert "secret SQLite path" not in reply.text
    assert reply.actions[0].command == "/source_content 87"
    assert not any(action.command.startswith(f"/{provider}_import") for action in reply.actions)


class Request:
    def __init__(self, result):
        self.result = result

    def execute(self):
        return self.result


@pytest.mark.parametrize("provider", ["drive", "gmail"])
def test_configured_search_reaches_all_pages_without_downloading(provider, tmp_path, monkeypatch):
    queries = []
    metadata_ids = []
    authorization_calls = []
    cursor_offsets = {None: 0, "opaque/second+=": 5, "opaque/third+=": 10}

    class Api:
        def files(self):
            return self

        def users(self):
            return self

        def messages(self):
            return self

        def list(self, **kwargs):
            queries.append(kwargs)
            offset = cursor_offsets[kwargs.get("pageToken")]
            assert kwargs.get("pageSize", kwargs.get("maxResults")) == 5
            items = [{"id": f"item-{index}", "name": f"Notes {index}.txt", "mimeType": "text/plain"}
                     for index in range(offset, min(offset + 5, 12))]
            result = {"files" if provider == "drive" else "messages": items}
            if offset < 10:
                result["nextPageToken"] = "opaque/second+=" if offset == 0 else "opaque/third+="
            return Request(result)

        def get(self, **kwargs):
            # No raw body or binary download is available on this fake.
            assert provider == "gmail"
            assert kwargs["format"] == "metadata"
            metadata_ids.append(kwargs["id"])
            return Request({"id": kwargs["id"], "threadId": "thread",
                            "payload": {"headers": [{"name": "Subject", "value": kwargs["id"]}]}})

    def authorize(client_path, token_path):
        assert client_path == tmp_path / "synthetic-client.json"
        authorization_calls.append(token_path)
        return Api()

    monkeypatch.setenv("STEWARD_GOOGLE_CLIENT_SECRETS", str(tmp_path / "synthetic-client.json"))
    monkeypatch.setattr(cli, "authorize_google_drive" if provider == "drive" else "authorize_gmail", authorize)
    settings = SimpleNamespace(data_dir=tmp_path)
    importer_type = cli._ConfiguredDriveInboxImporter if provider == "drive" else cli._ConfiguredGmailInboxImporter
    application_type = StewardDriveImportApplication if provider == "drive" else StewardGmailImportApplication
    database = tmp_path / "callbacks.db"
    initialize_database(database)
    command = f"/{provider}_search parallel notes"
    selected_ids = []

    for page_number in range(3):
        # Rebuild the application/composition for every click, as after restart.
        application = application_type(importer_type(settings, None))
        event = IncomingEvent(str(page_number), "telegram", "chat", str(page_number), None, datetime.now(UTC), command)
        reply = application.handle_command(event)
        assert isinstance(reply, PresentedReply)
        imports = [action for action in reply.actions if action.command.startswith(f"/{provider}_import ")]
        assert len(imports) == (5 if page_number < 2 else 2)
        selected_ids.extend(action.command.split(" ", 1)[1] for action in imports)
        assert len(queries) == page_number + 1  # No prefetch or account-wide traversal.
        more = [action for action in reply.actions if action.label == "More results"]
        if page_number < 2:
            saved = TelegramCallbackRepository(database).create("chat", more[0].command)
            command = TelegramCallbackRepository(database).resolve(saved.token, "chat").command
        else:
            assert more == []

    assert selected_ids == [f"item-{index}" for index in range(12)]
    assert len(authorization_calls) == 3
    assert all(request["q"] == ("trashed = false and name contains 'parallel notes'" if provider == "drive" else "parallel notes") for request in queries)
    assert metadata_ids == (selected_ids if provider == "gmail" else [])
