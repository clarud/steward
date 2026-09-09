"""Google Drive OAuth and read-only metadata search boundary."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Protocol

from steward.capture import CaptureResult, InboxCaptureService
from steward.events import IncomingEvent


GOOGLE_DRIVE_READONLY_SCOPE = "https://www.googleapis.com/auth/drive.readonly"
GOOGLE_NATIVE_EXPORTS = {
    "application/vnd.google-apps.document": ("text/plain", ".txt"),
    "application/vnd.google-apps.spreadsheet": ("text/csv", ".csv"),
    "application/vnd.google-apps.presentation": ("application/pdf", ".pdf"),
}


class DriveApi(Protocol):
    def files(self) -> Any: ...


@dataclass(frozen=True, slots=True)
class DriveFile:
    id: str
    name: str
    mime_type: str
    modified_time: str | None
    web_view_link: str | None
    size_bytes: int | None


class GoogleDriveService:
    """Read explicitly selected Drive files; Google Drive remains authoritative."""

    def __init__(self, client: DriveApi) -> None:
        self._client = client

    def search(self, query: str = "", *, limit: int = 10) -> tuple[DriveFile, ...]:
        if not 1 <= limit <= 100:
            raise ValueError("Drive result limit must be between 1 and 100.")
        clauses = ["trashed = false"]
        if query.strip():
            escaped = query.strip().replace("\\", "\\\\").replace("'", "\\'")
            clauses.append(f"name contains '{escaped}'")
        result = self._client.files().list(
            q=" and ".join(clauses),
            pageSize=limit,
            orderBy="modifiedTime desc",
            fields="files(id,name,mimeType,modifiedTime,webViewLink,size)",
        ).execute()
        return tuple(self._from_api(item) for item in result.get("files", []))

    def get_file(self, file_id: str) -> DriveFile:
        if not file_id.strip():
            raise ValueError("Drive file ID must not be empty.")
        return self._from_api(self._client.files().get(
            fileId=file_id, fields="id,name,mimeType,modifiedTime,webViewLink,size"
        ).execute())

    def download_to(self, file_id: str, destination: Path) -> None:
        """Stream one explicitly selected original from Drive to local storage."""
        from googleapiclient.http import MediaIoBaseDownload
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("wb") as output:
            downloader = MediaIoBaseDownload(output, self._client.files().get_media(fileId=file_id))
            complete = False
            while not complete:
                _, complete = downloader.next_chunk()

    def download_file_to(self, remote: DriveFile, destination: Path) -> None:
        """Download a binary original or one explicit export of a native Drive file."""
        from googleapiclient.http import MediaIoBaseDownload

        if remote.mime_type.startswith("application/vnd.google-apps.") and remote.mime_type not in GOOGLE_NATIVE_EXPORTS:
            raise ValueError(f"Drive native type {remote.mime_type!r} has no supported local export.")
        destination.parent.mkdir(parents=True, exist_ok=True)
        request = (
            self._client.files().export_media(fileId=remote.id, mimeType=GOOGLE_NATIVE_EXPORTS[remote.mime_type][0])
            if remote.mime_type in GOOGLE_NATIVE_EXPORTS
            else self._client.files().get_media(fileId=remote.id)
        )
        with destination.open("wb") as output:
            downloader = MediaIoBaseDownload(output, request)
            complete = False
            while not complete:
                _, complete = downloader.next_chunk()

    @staticmethod
    def import_name(remote: DriveFile) -> str:
        """Give explicitly exported native documents a format-bearing local name."""
        if remote.mime_type not in GOOGLE_NATIVE_EXPORTS:
            return remote.name
        suffix = GOOGLE_NATIVE_EXPORTS[remote.mime_type][1]
        return remote.name if Path(remote.name).suffix else remote.name + suffix

    @staticmethod
    def _from_api(item: dict[str, object]) -> DriveFile:
        identifier = item.get("id")
        name = item.get("name")
        mime_type = item.get("mimeType")
        if not all((identifier, name, mime_type)):
            raise ValueError("Drive file is missing required metadata.")
        size = item.get("size")
        return DriveFile(
            str(identifier), str(name), str(mime_type),
            str(item["modifiedTime"]) if item.get("modifiedTime") else None,
            str(item["webViewLink"]) if item.get("webViewLink") else None,
            int(str(size)) if size is not None else None,
        )


class DriveInboxImportService:
    """Copy one user-selected Drive original into Inbox before local extraction.

    The Google Drive ID is the import event's stable identity.  Repeating an
    import therefore reaches the same Inbox destination and is idempotent via
    ``InboxCaptureService``; it never creates an automatic background mirror.
    """

    def __init__(
        self, drive_service: GoogleDriveService, capture_service: InboxCaptureService
    ) -> None:
        self._drive = drive_service
        self._capture = capture_service

    def import_file(self, file_id: str) -> CaptureResult:
        remote = self._drive.get_file(file_id)
        local_name = self._drive.import_name(remote)
        suffix = Path(local_name).suffix
        with TemporaryDirectory() as temporary_dir:
            downloaded = Path(temporary_dir) / f"drive-download{suffix}"
            self._drive.download_file_to(remote, downloaded)
            return self._capture.capture_file(
                IncomingEvent(
                    id=f"drive:{remote.id}",
                    platform="drive",
                    chat_id="import",
                    message_id=remote.id,
                    reply_to_id=None,
                    timestamp=datetime.now().astimezone(),
                    text=None,
                    attachments=(local_name,),
                ),
                downloaded,
            )


def authorize_google_drive(client_secrets_path: Path, token_path: Path) -> DriveApi:
    """Run local OAuth if needed and create a read-only Google Drive client."""
    if not client_secrets_path.is_file():
        raise FileNotFoundError(client_secrets_path)
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build

    credentials = None
    if token_path.is_file():
        credentials = Credentials.from_authorized_user_file(
            str(token_path), (GOOGLE_DRIVE_READONLY_SCOPE,)
        )
        if credentials and not credentials.has_scopes((GOOGLE_DRIVE_READONLY_SCOPE,)):
            credentials = None
    if credentials and credentials.expired and credentials.refresh_token:
        credentials.refresh(Request())
    if not credentials or not credentials.valid:
        flow = InstalledAppFlow.from_client_secrets_file(
            str(client_secrets_path), (GOOGLE_DRIVE_READONLY_SCOPE,)
        )
        credentials = flow.run_local_server(port=0)
    token_path.parent.mkdir(parents=True, exist_ok=True)
    token_path.write_text(credentials.to_json(), encoding="utf-8")
    return build("drive", "v3", credentials=credentials)
