from pathlib import Path

import pytest

from steward.drive import authorize_google_drive
from steward.gmail import authorize_gmail


@pytest.mark.parametrize(
    "authorize,api_name,api_version",
    (
        (authorize_google_drive, "drive", "v3"),
        (authorize_gmail, "gmail", "v1"),
    ),
)
def test_revoked_google_token_restarts_local_browser_consent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, authorize: object,
    api_name: str, api_version: str,
) -> None:
    """A revoked token must open consent, rather than terminate the CLI."""
    from google.auth.exceptions import RefreshError
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    import googleapiclient.discovery

    client_secrets = tmp_path / "client.json"
    client_secrets.write_text("{}", encoding="utf-8")
    token_path = tmp_path / "token.json"
    token_path.write_text("old token", encoding="utf-8")

    class RevokedCredentials:
        expired = True
        refresh_token = "revoked"
        valid = False

        @staticmethod
        def has_scopes(_scopes: tuple[str, ...]) -> bool:
            return True

        @staticmethod
        def refresh(_request: object) -> None:
            raise RefreshError("invalid_grant")

    class FreshCredentials:
        expired = False
        refresh_token = None
        valid = True

        @staticmethod
        def to_json() -> str:
            return '{"fresh": true}'

    class Flow:
        ran = False

        @classmethod
        def run_local_server(cls, *, port: int) -> FreshCredentials:
            assert port == 0
            cls.ran = True
            return FreshCredentials()

    built = object()
    monkeypatch.setattr(
        Credentials, "from_authorized_user_file", lambda *_args, **_kwargs: RevokedCredentials()
    )
    monkeypatch.setattr(
        InstalledAppFlow, "from_client_secrets_file", lambda *_args, **_kwargs: Flow()
    )
    monkeypatch.setattr(
        googleapiclient.discovery, "build",
        lambda name, version, *, credentials: built if (name, version, credentials.__class__) == (api_name, api_version, FreshCredentials) else None,
    )

    result = authorize(client_secrets, token_path)  # type: ignore[operator]

    assert result is built
    assert Flow.ran is True
    assert token_path.read_text(encoding="utf-8") == '{"fresh": true}'
