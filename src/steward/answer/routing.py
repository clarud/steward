"""Select an appropriate model boundary for a set of source IDs."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from collections.abc import Iterable

from steward.answer.gateway import ModelGateway
from steward.privacy import PrivacyRule, PrivacyService


class ModelLocation(StrEnum):
    CLOUD = "cloud"
    LOCAL = "local"


class ModelRoutingError(RuntimeError):
    """The evidence policy requires a model Steward cannot currently use."""


@dataclass(frozen=True, slots=True)
class ModelSelection:
    gateway: ModelGateway
    location: ModelLocation


class ModelRouter:
    """Route content to cloud or local inference after applying privacy rules."""

    def __init__(
        self,
        privacy_service: PrivacyService,
        cloud_gateway: ModelGateway,
        local_gateway: ModelGateway | None = None,
    ) -> None:
        self._privacy = privacy_service
        self._cloud = cloud_gateway
        self._local = local_gateway

    def allows_any_model(self, source_id: int) -> bool:
        return self._privacy.permits_local_model(source_id)

    def select(self, source_ids: Iterable[int]) -> ModelSelection:
        """Choose local inference whenever any supplied source requires it."""

        rules = {self._privacy.rule_for(source_id) for source_id in source_ids}
        if PrivacyRule.NO_MODEL in rules:
            raise ValueError("No-model sources must be filtered before model routing.")
        if rules & {PrivacyRule.EXTERNAL_REDACTED, PrivacyRule.LOCAL_MODEL_ONLY}:
            if self._local is None:
                raise ModelRoutingError(
                    "The retrieved evidence requires a local model, but no local model is configured."
                )
            return ModelSelection(self._local, ModelLocation.LOCAL)
        return ModelSelection(self._cloud, ModelLocation.CLOUD)
