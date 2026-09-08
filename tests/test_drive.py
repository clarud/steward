import pytest

from steward.drive import GoogleDriveService


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
        "orderBy": "modifiedTime desc", "fields": "files(id,name,mimeType,modifiedTime,webViewLink,size)",
    }


def test_drive_search_escapes_query_and_validates_limit() -> None:
    client = FakeDrive()
    service = GoogleDriveService(client)
    service.search("Claire's \\ notes")
    assert "Claire\\'s \\\\ notes" in client.files_api.kwargs["q"]
    with pytest.raises(ValueError, match="between"):
        service.search(limit=0)
