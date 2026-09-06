"""A provider boundary for model text generation."""

from __future__ import annotations

from typing import Protocol


class ModelGateway(Protocol):
    """Generate text from explicitly supplied instructions and input."""

    def generate(self, *, instructions: str, input_text: str) -> str:
        """Return the model's completed text."""


class OpenAIModelGateway:
    """OpenAI Responses API gateway, isolated from Steward's domain services."""

    def __init__(self, *, api_key: str, model: str) -> None:
        if not api_key.strip():
            raise ValueError("An OpenAI API key is required.")
        if not model.strip():
            raise ValueError("An OpenAI model name is required.")

        # The import is local so non-answering commands do not require this SDK.
        from openai import OpenAI

        self._client = OpenAI(api_key=api_key)
        self._model = model

    def generate(self, *, instructions: str, input_text: str) -> str:
        """Generate one non-persisted response from supplied local evidence."""
        response = self._client.responses.create(
            model=self._model,
            instructions=instructions,
            input=input_text,
            store=False,
        )
        text = response.output_text.strip()
        if not text:
            raise RuntimeError("The model returned no text.")
        return text


class GeminiModelGateway:
    """Gemini Interactions API gateway isolated from Steward's domain services."""

    def __init__(self, *, api_key: str, model: str) -> None:
        if not api_key.strip():
            raise ValueError("A Gemini API key is required.")
        if not model.strip():
            raise ValueError("A Gemini model name is required.")

        # The import is local so non-answering commands do not require this SDK.
        from google import genai

        self._client = genai.Client(api_key=api_key)
        self._model = model

    def generate(self, *, instructions: str, input_text: str) -> str:
        """Generate one non-persisted interaction from supplied local evidence."""
        interaction = self._client.interactions.create(
            model=self._model,
            system_instruction=instructions,
            input=input_text,
            store=False,
        )
        text = interaction.output_text or ""
        if not text.strip():
            raise RuntimeError("The model returned no text.")
        return text.strip()
