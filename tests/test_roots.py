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


def test_source_root_persists_only_exclusions_beneath_its_authorized_path(tmp_path: Path) -> None:
    database_path = tmp_path / "steward.db"; initialize_database(database_path)
    root_path = tmp_path / "notes"; root_path.mkdir()
    excluded = root_path / "generated"; excluded.mkdir()

    root = SourceRootRepository(database_path).add("School", root_path, exclusions=(Path("generated"),))

    assert root.exclusions == (Path("generated"),)
    assert SourceRootRepository(database_path).get_by_name("School") == root
    with pytest.raises(ValueError, match="beneath"):
        SourceRootRepository(database_path).add("Bad", root_path, exclusions=(tmp_path,))
