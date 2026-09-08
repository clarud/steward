"""Google Gmail OAuth and read-only message-metadata boundary."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol


GOOGLE_GMAIL_READONLY_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"


class GmailApi(Protocol):
    def users(self) -> Any: ...


@dataclass(frozen=True, slots=True)
class GmailMessage:
    id: str
    thread_id: str
    subject: str
    sender: str | None
    received_at: str | None
    snippet: str


class GmailService:
    """Search current Gmail message metadata without changing mailbox state."""

    def __init__(self, client: GmailApi, *, user_id: str = "me") -> None:
        if not user_id.strip():
            raise ValueError("Gmail user ID must not be empty.")
        self._client = client
        self._user_id = user_id

    def search(self, query: str = "", *, limit: int = 10) -> tuple[GmailMessage, ...]:
        if not 1 <= limit <= 100:
            raise ValueError("Gmail result limit must be between 1 and 100.")
        request: dict[str, object] = {"userId": self._user_id, "maxResults": limit}
        if query.strip():
            request["q"] = query.strip()
        listed = self._client.users().messages().list(**request).execute()
        messages = listed.get("messages", [])
        return tuple(self._get_metadata(str(item["id"])) for item in messages if item.get("id"))

    def _get_metadata(self, message_id: str) -> GmailMessage:
        item = self._client.users().messages().get(
            userId=self._user_id,
            id=message_id,
            format="metadata",
            metadataHeaders=["Subject", "From", "Date"],
        ).execute()
        identifier = item.get("id")
        thread_id = item.get("threadId")
        if not identifier or not thread_id:
            raise ValueError("Gmail message is missing required identifiers.")
        headers = {
            str(header.get("name", "")).casefold(): str(header.get("value", ""))
            for header in (item.get("payload", {}).get("headers", []) if isinstance(item.get("payload"), dict) else [])
            if isinstance(header, dict)
        }
        return GmailMessage(
            str(identifier), str(thread_id), headers.get("subject") or "(no subject)",
            headers.get("from") or None, headers.get("date") or None, str(item.get("snippet") or ""),
        )


def authorize_gmail(client_secrets_path: Path, token_path: Path) -> GmailApi:
    """Run local OAuth if needed and create a read-only Gmail client."""
    if not client_secrets_path.is_file():
        raise FileNotFoundError(client_secrets_path)
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build

    credentials = None
    if token_path.is_file():
        credentials = Credentials.from_authorized_user_file(str(token_path), (GOOGLE_GMAIL_READONLY_SCOPE,))
        if credentials and not credentials.has_scopes((GOOGLE_GMAIL_READONLY_SCOPE,)):
            credentials = None
    if credentials and credentials.expired and credentials.refresh_token:
        credentials.refresh(Request())
    if not credentials or not credentials.valid:
        flow = InstalledAppFlow.from_client_secrets_file(str(client_secrets_path), (GOOGLE_GMAIL_READONLY_SCOPE,))
        credentials = flow.run_local_server(port=0)
    token_path.parent.mkdir(parents=True, exist_ok=True)
    token_path.write_text(credentials.to_json(), encoding="utf-8")
    return build("gmail", "v1", credentials=credentials)
