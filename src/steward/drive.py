"""Google Drive OAuth and read-only metadata search boundary."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol


GOOGLE_DRIVE_READONLY_SCOPE = "https://www.googleapis.com/auth/drive.readonly"


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
    """Search current Drive metadata; Google Drive remains authoritative."""

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
