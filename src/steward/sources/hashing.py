"""Content hashing for original source files."""

from __future__ import annotations

import hashlib
from pathlib import Path

HASH_CHUNK_SIZE = 64 * 1024


def hash_file(path: Path) -> str:
    """Return the lowercase SHA-256 digest of a file's exact bytes."""
    digest = hashlib.sha256()

    with path.open("rb") as source_file:
        while chunk := source_file.read(HASH_CHUNK_SIZE):
            digest.update(chunk)

    return digest.hexdigest()

