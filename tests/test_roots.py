from pathlib import Path
from datetime import UTC, datetime
from dataclasses import replace

import pytest

from steward.roots import SourceRootRepository
from steward.storage import initialize_database
from steward.sources import Source, SourceRepository, SourceStatus, SourceType
from steward.sources.hashing import hash_file


def registered_source(path: Path) -> Source:
    stat = path.stat(); now = datetime.now(UTC)
    return Source(None, path.resolve(), hash_file(path), SourceType.MARKDOWN, stat.st_size, now, now, now)


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


def test_source_root_can_be_disabled_without_removing_its_authorization(tmp_path: Path) -> None:
    database_path = tmp_path / "steward.db"; initialize_database(database_path)
    root_path = tmp_path / "notes"; root_path.mkdir()
    repository = SourceRootRepository(database_path)
    repository.add("School", root_path)

    disabled = repository.set_enabled("School", False)

    assert disabled.enabled is False
    assert disabled.health == "disabled"
    assert repository.get_by_name("School") == disabled


def test_source_root_reports_missing_when_an_enabled_path_disappears(tmp_path: Path) -> None:
    database_path = tmp_path / "steward.db"; initialize_database(database_path)
    root_path = tmp_path / "notes"; root_path.mkdir()
    root = SourceRootRepository(database_path).add("School", root_path)

    root_path.rmdir()

    assert root.health == "missing"


def test_missing_root_relocation_atomically_rebinds_matching_sources(tmp_path: Path) -> None:
    database = tmp_path / "steward.db"; initialize_database(database)
    old = tmp_path / "old"; old.mkdir(); nested = old / "course"; nested.mkdir()
    note = nested / "note.md"; note.write_text("# Exact original", encoding="utf-8")
    roots = SourceRootRepository(database); roots.add("School", old, exclusions=(Path("generated"),))
    sources = SourceRepository(database)
    source = sources.add(registered_source(note))
    sources.update(replace(source, status=SourceStatus.MISSING))
    new = tmp_path / "new"; new.mkdir(); replacement = new / "course"; replacement.mkdir()
    (replacement / "note.md").write_bytes(note.read_bytes())
    note.unlink(); nested.rmdir(); old.rmdir()

    result = roots.relocate_missing("School", new)

    assert result.updated_sources == 1
    assert result.root.path == new.resolve()
    assert result.root.exclusions == (Path("generated"),)
    relocated_source = sources.get_by_id(source.id)
    assert relocated_source.path == (replacement / "note.md").resolve()
    assert relocated_source.status is SourceStatus.ACTIVE


@pytest.mark.parametrize("failure", ["missing", "changed"])
def test_root_relocation_mismatch_rolls_back_all_paths(tmp_path: Path, failure: str) -> None:
    database = tmp_path / "steward.db"; initialize_database(database)
    old = tmp_path / "old"; old.mkdir()
    first = old / "first.md"; first.write_text("first", encoding="utf-8")
    second = old / "second.md"; second.write_text("second", encoding="utf-8")
    roots = SourceRootRepository(database); original_root = roots.add("School", old)
    sources = SourceRepository(database)
    registered = [sources.add(registered_source(path)) for path in (first, second)]
    new = tmp_path / "new"; new.mkdir()
    (new / "first.md").write_text("first", encoding="utf-8")
    if failure == "changed":
        (new / "second.md").write_text("changed", encoding="utf-8")
    first.unlink(); second.unlink(); old.rmdir()

    with pytest.raises(ValueError, match="missing tracked|does not match"):
        roots.relocate_missing("School", new)

    assert roots.get_by_name("School") == original_root
    assert [sources.get_by_id(item.id).path for item in registered] == [first.resolve(), second.resolve()]


def test_root_relocation_refuses_available_or_already_authorized_destination(tmp_path: Path) -> None:
    database = tmp_path / "steward.db"; initialize_database(database)
    first = tmp_path / "first"; first.mkdir()
    second = tmp_path / "second"; second.mkdir()
    roots = SourceRootRepository(database); roots.add("First", first); roots.add("Second", second)
    with pytest.raises(ValueError, match="still available"):
        roots.relocate_missing("First", second)
    first.rmdir()
    with pytest.raises(ValueError, match="already authorized"):
        roots.relocate_missing("First", second)


def test_root_relocation_refuses_registered_destination_path_without_partial_update(tmp_path: Path) -> None:
    database = tmp_path / "steward.db"; initialize_database(database)
    old = tmp_path / "old"; old.mkdir(); original = old / "note.md"; original.write_text("same", encoding="utf-8")
    new = tmp_path / "new"; new.mkdir(); candidate = new / "note.md"; candidate.write_text("same", encoding="utf-8")
    roots = SourceRootRepository(database); prior_root = roots.add("School", old)
    sources = SourceRepository(database)
    old_source = sources.add(registered_source(original)); other_source = sources.add(registered_source(candidate))
    original.unlink(); old.rmdir()

    with pytest.raises(ValueError, match="already registered"):
        roots.relocate_missing("School", new)

    assert roots.get_by_name("School") == prior_root
    assert sources.get_by_id(old_source.id).path == original.resolve()
    assert sources.get_by_id(other_source.id).path == candidate.resolve()
