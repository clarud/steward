"""Ask a model for JSON that code validates; repair once, then let the caller fall back."""

from __future__ import annotations

import json
import threading
from collections.abc import Callable
from typing import TypeVar

from steward.answer.gateway import ModelGateway, ModelGatewayError
from steward.observability import trace

T = TypeVar("T")


class StructuredOutputError(RuntimeError):
    """The model could not produce valid output; callers use their plain fallback."""


class BudgetExhausted(StructuredOutputError):
    """The flow already spent its model calls."""


class CallBudget:
    """A hard cap on model calls for one request, shared by every role in a flow."""

    def __init__(self, limit: int) -> None:
        if limit < 0:
            raise ValueError("A call budget cannot be negative.")
        self.limit = limit
        self.used = 0
        self._lock = threading.Lock()  # parallel workers share one budget

    @property
    def remaining(self) -> int:
        return self.limit - self.used

    def spend(self) -> None:
        with self._lock:
            if self.used >= self.limit:
                raise BudgetExhausted("This request has used all of its model calls.")
            self.used += 1


def generate_text(model: ModelGateway, budget: CallBudget, *, role: str, instructions: str, input_text: str) -> str:
    """One budgeted model call; provider failures become StructuredOutputError."""
    budget.spend()
    try:
        return model.generate(instructions=instructions, input_text=input_text)
    except ModelGatewayError as error:
        trace("role.unavailable", role=role)
        raise StructuredOutputError(f"{role}: model unavailable") from error


def generate_json(
    model: ModelGateway,
    budget: CallBudget,
    *,
    role: str,
    instructions: str,
    input_text: str,
    contract: str,
    validate: Callable[[object], T],
) -> T:
    """Call ``model`` for a JSON object and return ``validate(parsed)``.

    ``contract`` describes the expected JSON shape in the prompt. ``validate``
    raises ValueError with a readable reason when the output is unusable. One
    repair call quotes that reason; if it fails again, StructuredOutputError
    tells the caller to use its deterministic fallback.
    """
    prompt = (
        f"{instructions}\n\nReply with only one JSON object, no prose and no code fences. "
        f"Required shape:\n{contract}"
    )
    output = generate_text(model, budget, role=role, instructions=prompt, input_text=input_text)
    try:
        return validate(_parse_object(output))
    except ValueError as first_error:
        reason = str(first_error)
    trace("role.repair", role=role)
    if budget.remaining <= 0:
        raise StructuredOutputError(f"{role}: invalid output and no budget to repair ({reason})")
    repaired = generate_text(
        model, budget, role=role, instructions=prompt,
        input_text=f"{input_text}\n\nYour previous reply was rejected: {reason}\nPrevious reply:\n{output[:2000]}",
    )
    try:
        return validate(_parse_object(repaired))
    except ValueError as error:
        trace("role.fallback", role=role)
        raise StructuredOutputError(f"{role}: invalid output after repair ({error})") from error


def _parse_object(output: str) -> dict:
    start, end = output.find("{"), output.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("no JSON object found")
    try:
        parsed = json.loads(output[start:end + 1])
    except json.JSONDecodeError as error:
        raise ValueError(f"invalid JSON ({error.msg})") from error
    if not isinstance(parsed, dict):
        raise ValueError("expected a JSON object")
    return parsed
