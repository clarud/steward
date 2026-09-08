import pytest
from base64 import urlsafe_b64encode

from steward.gmail import GmailService


class Request:
    def __init__(self, result): self._result = result
    def execute(self): return self._result


class Messages:
    def __init__(self): self.list_kwargs = None; self.get_kwargs = []
    def list(self, **kwargs):
        self.list_kwargs = kwargs
        return Request({"messages": [{"id": "mail-1"}]})
    def get(self, **kwargs):
        self.get_kwargs.append(kwargs)
        return Request({"id": "mail-1", "threadId": "thread-1", "snippet": "Your flight changed", "payload": {"headers": [
            {"name": "Subject", "value": "Flight update"}, {"name": "From", "value": "Airline <a@example.com>"},
            {"name": "Date", "value": "Tue, 9 Sep 2026 10:00:00 +0800"},
        ]}})


class Users:
    def __init__(self, messages): self._messages = messages
    def messages(self): return self._messages


class Client:
    def __init__(self): self.messages_api = Messages()
    def users(self): return Users(self.messages_api)


def test_gmail_search_reads_only_message_metadata() -> None:
    client = Client()
    messages = GmailService(client).search("from:airline", limit=2)

    assert messages[0].subject == "Flight update"
    assert client.messages_api.list_kwargs == {"userId": "me", "maxResults": 2, "q": "from:airline"}
    assert client.messages_api.get_kwargs[0]["format"] == "metadata"
    assert client.messages_api.get_kwargs[0]["metadataHeaders"] == ["Subject", "From", "Date"]


def test_gmail_search_validates_limit() -> None:
    with pytest.raises(ValueError, match="between"):
        GmailService(Client()).search(limit=101)


def test_gmail_download_raw_decodes_an_explicit_message() -> None:
    client = Client()
    raw = b"Subject: Flight\n\nChanged"
    original_get = client.messages_api.get
    client.messages_api.get = lambda **kwargs: Request({"raw": urlsafe_b64encode(raw).decode().rstrip("=")})

    assert GmailService(client).download_raw("mail-1") == raw
    client.messages_api.get = original_get
