import pytest

from steward.capture import CaptureResult
from steward.drive import DriveFile, DriveInboxImportService, GoogleDriveService


class FakeRequest:
    def execute(self):
        return {"files": [{
            "id": "drive-1", "name": "Itinerary.pdf", "mimeType": "application/pdf",
            "modifiedTime": "2026-09-09T01:00:00Z", "webViewLink": "https://drive.example/1", "size": "42",
        }]}


class FakeFiles:
    def __init__(self) -> None:
        self.kwargs = None

    def list(self, **kwargs):
        self.kwargs = kwargs
        return FakeRequest()


class FakeDrive:
    def __init__(self) -> None:
        self.files_api = FakeFiles()

    def files(self):
        return self.files_api


def test_drive_search_requests_current_metadata_without_downloading_content() -> None:
    client = FakeDrive()
    files = GoogleDriveService(client).search("itinerary", limit=3)

    assert files[0].name == "Itinerary.pdf"
    assert files[0].size_bytes == 42
    assert client.files_api.kwargs == {
        "q": "trashed = false and name contains 'itinerary'", "pageSize": 3,
        "orderBy": "modifiedTime desc", "fields": "nextPageToken,files(id,name,mimeType,modifiedTime,webViewLink,size)",
    }


def test_drive_search_escapes_query_and_validates_limit() -> None:
    client = FakeDrive()
    service = GoogleDriveService(client)
    service.search("Claire's \\ notes")
    assert "Claire\\'s \\\\ notes" in client.files_api.kwargs["q"]
    with pytest.raises(ValueError, match="between"):
        service.search(limit=0)


def test_drive_search_page_preserves_empty_page_continuation() -> None:
    client = FakeDrive()
    class Request:
        def execute(self):
            return {"files": [], "nextPageToken": "next/opaque=="}
    def listed(**kwargs):
        assert kwargs["pageToken"] == "previous"
        assert kwargs["pageSize"] == 5
        return Request()
    client.files_api.list = listed
    page = GoogleDriveService(client).search_page("notes", page_token="previous")
    assert page.items == ()
    assert page.next_page_token == "next/opaque=="


def test_explicit_drive_import_downloads_then_captures_with_a_stable_event(tmp_path) -> None:
    class ImportDrive:
        def get_file(self, file_id: str) -> DriveFile:
            assert file_id == "drive-42"
            return DriveFile("drive-42", "Flight plan.pdf", "application/pdf", None, None, 3)

        def import_name(self, remote: DriveFile) -> str:
            return remote.name

        def download_file_to(self, remote: DriveFile, destination) -> None:
            assert remote.id == "drive-42"
            destination.write_bytes(b"PDF")

    class Capture:
        def __init__(self) -> None:
            self.event = None
            self.contents = None

        def capture_file(self, event, path):
            self.event = event
            self.contents = path.read_bytes()
            return CaptureResult(type("Source", (), {"path": tmp_path / "inbox" / "drive-import-drive-42-Flight-plan.pdf"})(), False)

    capture = Capture()
    result = DriveInboxImportService(ImportDrive(), capture).import_file("drive-42")

    assert result.duplicate is False
    assert capture.contents == b"PDF"
    assert capture.event.id == "drive:drive-42"
    assert capture.event.platform == "drive"
    assert capture.event.attachments == ("Flight plan.pdf",)


def test_native_drive_documents_receive_an_explicit_export_extension() -> None:
    document = DriveFile("doc-1", "Project notes", "application/vnd.google-apps.document", None, None, None)
    sheet = DriveFile("sheet-1", "Budget", "application/vnd.google-apps.spreadsheet", None, None, None)

    assert GoogleDriveService.import_name(document) == "Project notes.txt"
    assert GoogleDriveService.import_name(sheet) == "Budget.csv"


def test_unsupported_native_drive_type_is_rejected_before_download(tmp_path) -> None:
    drawing = DriveFile("drawing-1", "Architecture", "application/vnd.google-apps.drawing", None, None, None)

    with pytest.raises(ValueError, match="no supported local export"):
        GoogleDriveService(FakeDrive()).download_file_to(drawing, tmp_path / "drawing")
