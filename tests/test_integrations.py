import pytest
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
