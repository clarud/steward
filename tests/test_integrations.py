import pytest
import json
from datetime import UTC, datetime
from pathlib import Path

from steward.integrations import IntegrationDefinition, IntegrationRegistry, oauth_token_readiness
from steward.tools import ToolDefinition, ToolRisk


def test_oauth_token_readiness_is_secret_free_and_identifies_reauthorization(tmp_path: Path) -> None:
    missing = tmp_path / "missing.json"
    malformed = tmp_path / "malformed.json"; malformed.write_text("not-json-secret", encoding="utf-8")
    expired = tmp_path / "expired.json"; expired.write_text(
        '{"token":"secret","expiry":"2026-09-01T00:00:00+00:00"}', encoding="utf-8"
    )
    refreshable = tmp_path / "refreshable.json"; refreshable.write_text(
        '{"token":"secret","refresh_token":"refresh-secret","expiry":"2026-09-01T00:00:00+00:00"}', encoding="utf-8"
    )

    now = datetime(2026, 9, 10, tzinfo=UTC)
    assert oauth_token_readiness(missing, now=now) == "needs local browser authorization"
    assert oauth_token_readiness(malformed, now=now) == "local token present (readiness cannot be verified)"
    assert oauth_token_readiness(expired, now=now) == "local token expired; reauthorize locally"
    assert oauth_token_readiness(refreshable, now=now) == "local token expired; refresh available locally"


def test_oauth_token_readiness_detects_missing_required_scope_without_disclosing_it(tmp_path: Path) -> None:
    insufficient = tmp_path / "insufficient.json"
    insufficient.write_text(
        '{"token":"secret","expiry":"2026-10-01T00:00:00+00:00","scopes":["metadata-only"]}',
        encoding="utf-8",
    )
    sufficient = tmp_path / "sufficient.json"
    sufficient.write_text(
        '{"token":"secret","expiry":"2026-10-01T00:00:00+00:00","scopes":["required-scope"]}',
        encoding="utf-8",
    )
    unknown = tmp_path / "unknown.json"
    unknown.write_text(
        '{"token":"secret","expiry":"2026-10-01T00:00:00+00:00"}', encoding="utf-8"
    )
    now = datetime(2026, 9, 10, tzinfo=UTC)

    assert oauth_token_readiness(insufficient, now=now, required_scopes=("required-scope",)) == (
        "local token lacks required access; reauthorize locally"
    )
    assert oauth_token_readiness(sufficient, now=now, required_scopes=("required-scope",)) == "local token present"
    assert oauth_token_readiness(unknown, now=now, required_scopes=("required-scope",)) == (
        "local token present (required access cannot be verified)"
    )


def test_oauth_token_readiness_accepts_one_of_multiple_safe_scope_sets(tmp_path: Path) -> None:
    read_token = tmp_path / "read.json"
    write_token = tmp_path / "write.json"
    insufficient = tmp_path / "insufficient.json"
    for path, scopes in (
        (read_token, ["calendar.read"]),
        (write_token, ["calendar.write"]),
        (insufficient, ["metadata-only"]),
    ):
        path.write_text(json.dumps({"expiry": "2026-10-01T00:00:00+00:00", "scopes": scopes}), encoding="utf-8")
    now = datetime(2026, 9, 10, tzinfo=UTC)
    alternatives = (("calendar.read",), ("calendar.write",))

    assert oauth_token_readiness(read_token, now=now, any_required_scope_sets=alternatives) == "local token present"
    assert oauth_token_readiness(write_token, now=now, any_required_scope_sets=alternatives) == "local token present"
    assert oauth_token_readiness(insufficient, now=now, any_required_scope_sets=alternatives) == (
        "local token lacks required access; reauthorize locally"
    )


@pytest.mark.parametrize("expiry", [None, "malformed", "2026-09-01T00:00:00+00:00"])
@pytest.mark.parametrize("scopes", [["metadata-only"], "metadata-only"])
def test_missing_oauth_access_takes_priority_over_refresh_and_expiry(tmp_path: Path, expiry, scopes) -> None:
    token = tmp_path / "token.json"
    token.write_text(json.dumps({"expiry": expiry, "scopes": scopes, "refresh_token": "secret"}), encoding="utf-8")
    assert oauth_token_readiness(token, now=datetime(2026, 9, 10, tzinfo=UTC), required_scopes=("download",)) == (
        "local token lacks required access; reauthorize locally"
    )


def test_expired_token_with_required_access_remains_refreshable(tmp_path: Path) -> None:
    token = tmp_path / "token.json"
    token.write_text(json.dumps({"expiry": "2026-09-01T00:00:00+00:00", "scopes": ["download"], "refresh_token": "secret"}), encoding="utf-8")
    assert oauth_token_readiness(token, now=datetime(2026, 9, 10, tzinfo=UTC), required_scopes=("download",)) == (
        "local token expired; refresh available locally"
    )


def test_integration_registry_requires_external_tool_policy() -> None:
    registry = IntegrationRegistry()
    calendar = IntegrationDefinition("google_calendar", "GoogleCalendarApi", "CalendarService", ("calendar_search",), True)
    policy = ToolDefinition("calendar_search", False, ToolRisk.READ_ONLY, "not applicable", "google_calendar", False)

    registry.register(calendar, [policy])

    assert registry.list_all() == (calendar,)


def test_integration_registry_rejects_missing_or_duplicate_registration() -> None:
    registry = IntegrationRegistry()
    integration = IntegrationDefinition("gmail", "GmailApi", "GmailService", ("gmail_search",), True)
    with pytest.raises(ValueError, match="unregistered"):
        registry.register(integration, [])
    registry.register(integration, [ToolDefinition("gmail_search", False, ToolRisk.READ_ONLY, "not applicable", "gmail", False)])
    with pytest.raises(ValueError, match="already registered"):
        registry.register(integration, [])
