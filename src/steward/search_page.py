"""One bounded page from an external metadata search."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SearchPage[T]:
    items: tuple[T, ...]
    next_page_token: str | None = None
