"""Filesystem discovery of supported source files."""

from __future__ import annotations

from pathlib import Path


def discover_markdown_files(root: Path) -> list[Path]:
    """Return Markdown files beneath root as sorted, absolute paths."""
    if not root.exists():
        raise FileNotFoundError(f"Source root does not exist: {root}")
    if not root.is_dir():
        raise NotADirectoryError(f"Source root is not a directory: {root}")

    resolved_root = root.resolve()
    markdown_paths = (
        path.resolve()
        for path in resolved_root.rglob("*")
        if path.is_file() and path.suffix.casefold() == ".md"
    )
    return sorted(markdown_paths, key=lambda path: path.as_posix().casefold())

