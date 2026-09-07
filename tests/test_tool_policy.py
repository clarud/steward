import pytest

from steward.tools import ToolDefinition, ToolPolicy, ToolRisk


def test_read_only_tool_is_permitted_without_approval() -> None:
    policy = ToolPolicy([
        ToolDefinition("search_sources", False, ToolRisk.READ_ONLY, "not applicable", None, False)
    ])

    decision = policy.authorize("search_sources")

    assert decision.allowed is True


def test_unknown_and_duplicate_tools_are_rejected() -> None:
    definition = ToolDefinition("search_sources", False, ToolRisk.READ_ONLY, "not applicable", None, False)
    with pytest.raises(ValueError, match="unique"):
        ToolPolicy([definition, definition])
    with pytest.raises(ValueError, match="not registered"):
        ToolPolicy([definition]).authorize("delete_source")


def test_read_only_definition_cannot_claim_side_effects() -> None:
    with pytest.raises(ValueError, match="read-only"):
        ToolDefinition("broken", True, ToolRisk.READ_ONLY, "not applicable", None, False)
