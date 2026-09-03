from pathlib import Path

from steward.sources import hash_file


def test_hash_file_returns_sha256_for_exact_file_bytes(tmp_path: Path) -> None:
    source_path = tmp_path / "note.md"
    source_path.write_bytes(b"abc")

    assert hash_file(source_path) == (
        "ba7816bf8f01cfea414140de5dae2223"
        "b00361a396177a9cb410ff61f20015ad"
    )


def test_hash_file_changes_when_file_contents_change(tmp_path: Path) -> None:
    source_path = tmp_path / "note.md"
    source_path.write_bytes(b"first version")
    first_hash = hash_file(source_path)

    source_path.write_bytes(b"second version")

    assert hash_file(source_path) != first_hash

