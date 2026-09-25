"""Clear errors for features whose packages are optional extras."""

from __future__ import annotations


class MissingExtraError(RuntimeError):
    """An optional feature was used without its extra installed."""

    def __init__(self, extra: str, feature: str) -> None:
        super().__init__(
            f"{feature} needs optional packages. Install them with: pip install \"steward[{extra}]\""
        )
        self.extra = extra
