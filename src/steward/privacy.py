"""Per-source data-boundary policy evaluated before model use."""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path
import sqlite3


class PrivacyRule(StrEnum):
    EXTERNAL_ALLOWED = "external_allowed"
    EXTERNAL_REDACTED = "external_redacted"
    LOCAL_MODEL_ONLY = "local_model_only"
    NO_MODEL = "no_model"


class PrivacyService:
    """Store source-level rules at the boundary before model context is built."""

    def __init__(self, database_path: Path) -> None:
        self._database_path = database_path

    def set_rule(self, source_id: int, rule: PrivacyRule) -> None:
        with sqlite3.connect(self._database_path) as connection:
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute(
                "INSERT OR REPLACE INTO source_privacy_policies (source_id, rule) VALUES (?, ?)",
                (source_id, rule.value),
            )

    def rule_for(self, source_id: int) -> PrivacyRule:
        with sqlite3.connect(self._database_path) as connection:
            row = connection.execute(
                "SELECT rule FROM source_privacy_policies WHERE source_id = ?", (source_id,)
            ).fetchone()
        return PrivacyRule(str(row[0])) if row else PrivacyRule.EXTERNAL_ALLOWED

    def permits_external_model(self, source_id: int) -> bool:
        """Whether raw source content may be sent to a cloud model.

        ``external_redacted`` deliberately remains false until Steward has a
        real, reviewable redaction implementation. Failing closed avoids
        treating a label as if it had transformed private content.
        """
        return self.rule_for(source_id) is PrivacyRule.EXTERNAL_ALLOWED
