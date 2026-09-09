"""Filesystem discovery of supported source files."""

from __future__ import annotations

from pathlib import Path

from steward.sources.models import SourceType


SUPPORTED_SOURCE_TYPES = {
    ".md": SourceType.MARKDOWN,
    ".txt": SourceType.PLAIN_TEXT,
    ".csv": SourceType.PLAIN_TEXT,
    ".eml": SourceType.PLAIN_TEXT,
    ".pdf": SourceType.PDF,
    ".docx": SourceType.DOCX,
    ".html": SourceType.HTML,
    ".htm": SourceType.HTML,
    ".png": SourceType.IMAGE,
    ".jpg": SourceType.IMAGE,
    ".jpeg": SourceType.IMAGE,
    ".tif": SourceType.IMAGE,
    ".tiff": SourceType.IMAGE,
    ".bmp": SourceType.IMAGE,
    ".webp": SourceType.IMAGE,
}


def source_type_for_path(path: Path) -> SourceType | None:
    """Return the supported type inferred from one filename suffix."""

    return SUPPORTED_SOURCE_TYPES.get(path.suffix.casefold())


DEFAULT_EXCLUDED_DIRECTORY_NAMES = frozenset({".git", ".steward", ".venv", "__pycache__"})


def discover_source_files(root: Path, *, exclusions: tuple[Path, ...] = ()) -> list[Path]:
    """Return every currently folder-scannable source beneath ``root``."""

    if not root.exists():
        raise FileNotFoundError(f"Source root does not exist: {root}")
    if not root.is_dir():
        raise NotADirectoryError(f"Source root is not a directory: {root}")
    resolved_root = root.resolve()
    excluded_paths = tuple((item if item.is_absolute() else resolved_root / item).resolve() for item in exclusions)

    def is_excluded(path: Path) -> bool:
        return any(path.is_relative_to(exclusion) for exclusion in excluded_paths) or any(
            parent.name in DEFAULT_EXCLUDED_DIRECTORY_NAMES for parent in path.parents
        )

    paths = (
        path.resolve()
        for path in resolved_root.rglob("*")
        if path.is_file() and source_type_for_path(path) is not None and not is_excluded(path.resolve())
    )
    return sorted(paths, key=lambda path: path.as_posix().casefold())


def discover_markdown_files(root: Path, *, exclusions: tuple[Path, ...] = ()) -> list[Path]:
    """Return Markdown files beneath root as sorted, absolute paths."""
    return [path for path in discover_source_files(root, exclusions=exclusions) if path.suffix.casefold() == ".md"]
