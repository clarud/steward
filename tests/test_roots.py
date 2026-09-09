from pathlib import Path

import pytest

from steward.roots import SourceRootRepository
from steward.storage import initialize_database


def test_local_source_root_requires_existing_directory_and_is_persisted(tmp_path: Path) -> None:
    database_path = tmp_path / "steward.db"; initialize_database(database_path)
    root_path = tmp_path / "notes"; root_path.mkdir()
    repository = SourceRootRepository(database_path)

    root = repository.add("School Notes", root_path)

    assert root.path == root_path.resolve()
    assert repository.list_all() == (root,)
    with pytest.raises(ValueError, match="existing directory"):
        repository.add("Missing", tmp_path / "missing")
