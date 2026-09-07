from pathlib import Path

from tests.evaluation.product_evaluator import load_product_evaluation_inventory


def test_product_evaluation_inventory_covers_each_phase_34_subsystem() -> None:
    inventory = load_product_evaluation_inventory(
        Path(__file__).with_name("product_cases.yaml")
    )

    assert inventory.case_counts == {
        "organization": 2,
        "records": 1,
        "knowledge_integration": 1,
        "tool_calling": 1,
        "agent_safety": 2,
    }
    assert inventory.total_cases == 7
