"""Grounded answers built from Steward's retrieved local evidence."""

from steward.answer.context import ContextBuilder
from steward.answer.gateway import GeminiModelGateway, ModelGateway, ModelGatewayError, OllamaModelGateway, OpenAIModelGateway
from steward.answer.routing import ModelLocation, ModelRouter, ModelRoutingError
from steward.answer.models import AnswerCitation, AnswerContext, AnswerResult
from steward.answer.service import AnswerService

__all__ = [
    "AnswerCitation",
    "AnswerContext",
    "AnswerResult",
    "AnswerService",
    "ContextBuilder",
    "GeminiModelGateway",
    "ModelGateway",
    "ModelGatewayError",
    "ModelLocation",
    "ModelRouter",
    "ModelRoutingError",
    "OllamaModelGateway",
    "OpenAIModelGateway",
]
