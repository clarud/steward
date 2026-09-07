import pytest

from steward.integrations import IntegrationDefinition, IntegrationRegistry
from steward.tools import ToolDefinition, ToolRisk


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
