from pathlib import Path
from steward.actions import FileMutationService

def test_move_returns_rollback_data_and_undo_restores_original(tmp_path: Path) -> None:
    original = tmp_path / "inbox" / "note.md"; original.parent.mkdir(); original.write_text("note")
    destination = tmp_path / "projects" / "Steward" / "note.md"
    service = FileMutationService()
    result = service.move_source(original, destination)
    assert destination.is_file() and not original.exists()
    undone = service.undo_move(result)
    assert original.is_file() and undone.status == "undone"
