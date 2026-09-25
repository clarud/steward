"""Telegram use cases: routing, browsing, file cards, uploads, and /find."""

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from steward.activity import ActivityService
from steward.app import StewardEventApplication, StewardFilesApplication, StewardIntakeApplication
from steward.capture import InboxCaptureService
from steward.events import IncomingEvent
from steward.extraction import ExtractionResult, SourceFragment, SourceFragmentRepository
from steward.intake import ProvisionalIntakeRepository, ProvisionalIntakeService
from steward.presentation import PresentedReply
from steward.graphs.find import FindScope
from steward.reviews import ReviewContextRepository
from steward.roots import SourceRootRepository
from steward.sources import InboxQueue, Source, SourceRepository, SourceType
from steward.sources.export import SourceExportService
from steward.sources.hashing import hash_file
from steward.sources.inbox_context import SourceInboxContextRepository
from steward.storage import initialize_database

NOW = datetime(2026, 9, 24, tzinfo=UTC)


class FakeAnswers:
    """Records what the router hands to the model flows; the flows have their own tests."""

    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def find(self, chat_id, request, scope, roots=()):
        self.calls.append(("find", chat_id, request, scope))
        return PresentedReply("found", title="Found")

    def ask(self, chat_id, question, *, source=None):
        self.calls.append(("ask", chat_id, question, source.id if source else None))
        return PresentedReply("answered", title="Answer")

    def summarize(self, source, chat_id=None):
        self.calls.append(("summarize", source.id))
        return PresentedReply("summary", title=f"Summary: {source.path.name}")


def event(text: str | None = None, *, message_id: str = "7", reply_to: str | None = None, chat: str = "100") -> IncomingEvent:
    return IncomingEvent(f"telegram:{message_id}", "telegram", chat, message_id, reply_to, NOW, text)


class World:
    """A temporary Steward with one root (Y4S1/CS3210/...), an Inbox, and the app layer."""

    def __init__(self, tmp_path: Path) -> None:
        self.database = tmp_path / "steward.db"
        initialize_database(self.database)
        self.root_path = tmp_path / "Y4S1"
        self.inbox = tmp_path / "vault" / "inbox"
        (self.root_path / "CS3210" / "Tutorials").mkdir(parents=True)
        self.inbox.mkdir(parents=True)
        self.sources = SourceRepository(self.database)
        self.fragments = SourceFragmentRepository(self.database)
        self.roots = SourceRootRepository(self.database)
        self.root = self.roots.add("Y4S1", self.root_path)
        self.contexts = ReviewContextRepository(self.database)
        self.inbox_contexts = SourceInboxContextRepository(self.database)
        self.answers = FakeAnswers()
        activity = ActivityService(self.database)
        queue = InboxQueue(self.sources, self.inbox, roots=self.roots, inbox_contexts=self.inbox_contexts)
        capture = InboxCaptureService(self.inbox, self.sources, self.fragments, activity, queue)
        self.files = StewardFilesApplication(
            self.sources, self.fragments, self.roots, self.inbox,
            contexts=self.contexts, inbox_contexts=self.inbox_contexts,
            source_export=SourceExportService(self.sources, self.roots, self.inbox),
            answers=self.answers,
        )
        self.app = StewardEventApplication(
            files=self.files,
            intake=StewardIntakeApplication(
                ProvisionalIntakeService(
                    tmp_path / "cache", ProvisionalIntakeRepository(self.database), capture, activity,
                    roots=self.roots, inbox_contexts=self.inbox_contexts,
                ),
                contexts=self.contexts, roots=self.roots,
            ),
            answers=self.answers,
            roots=self.roots,
        )

    def add_file(self, relative: str, *texts: str, under: Path | None = None) -> Source:
        path = (under or self.root_path) / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(texts) or "x", encoding="utf-8")
        source = self.sources.add(Source(
            None, path.resolve(), hash_file(path), SourceType.MARKDOWN,
            path.stat().st_size, NOW, NOW, NOW,
        ))
        if texts:
            self.fragments.replace_for_source(ExtractionResult(source.id or 0, tuple(
                SourceFragment(None, source.id or 0, f"Part {index}", index, text, f"page {index + 1}")
                for index, text in enumerate(texts)
            )))
        return source


def commands(reply: PresentedReply) -> list[str]:
    return [action.command for action in reply.actions]


# -- routing -------------------------------------------------------------------

def test_plain_text_offers_find_ask_or_save_without_guessing(tmp_path: Path) -> None:
    world = World(tmp_path)

    reply = world.app.handle(event("tut 4 AVX question"))

    assert isinstance(reply, PresentedReply)
    assert commands(reply) == ["/find tut 4 AVX question", "/ask tut 4 AVX question", "/note tut 4 AVX question"]
    assert world.answers.calls == []


def test_ask_command_goes_to_the_ask_flow(tmp_path: Path) -> None:
    world = World(tmp_path)

    assert world.app.handle(event("/ask what is a TLB?")).text == "answered"
    assert world.answers.calls == [("ask", "100", "what is a TLB?", None)]
    assert "Use /ask" in world.app.handle(event("/ask"))


def test_unknown_commands_get_a_hint_and_home_offers_browse(tmp_path: Path) -> None:
    world = World(tmp_path)

    assert "Try /help" in world.app.handle(event("/tasks"))
    home = world.app.handle(event("/start"))
    assert isinstance(home, PresentedReply) and commands(home) == ["/sources", "/inbox", "/help"]
    assert "/find" in world.app.handle(event("/help"))


# -- browsing ------------------------------------------------------------------

def test_browse_goes_from_roots_to_folders_to_files_with_up_navigation(tmp_path: Path) -> None:
    world = World(tmp_path)
    world.add_file("CS3210/PROGRESS.md", "week 4")
    tutorial = world.add_file("CS3210/Tutorials/tut04.md", "AVX2 registers")
    world.add_file("README.md")

    overview = world.app.handle(event("/sources"))
    assert "Y4S1 · 3 files" in overview.text and "Inbox · 0 waiting" in overview.text

    top = world.app.handle(event(overview.actions[0].command))
    assert top.title == "Y4S1"
    assert top.text.splitlines() == ["1. 📁 CS3210 (2)", "2. 📄 README.md · no text"]
    assert top.actions[-1].command == "/sources"

    course = world.app.handle(event(top.actions[0].command))
    assert course.title == "Y4S1 / CS3210"
    assert course.text.splitlines() == ["1. 📁 Tutorials (1)", "2. 📄 PROGRESS.md"]
    assert course.actions[-1].label == "Up"

    tutorials = world.app.handle(event(course.actions[0].command))
    assert tutorials.actions[0].command == f"/source {tutorial.id}"
    assert world.app.handle(event(tutorials.actions[-1].command)).title == "Y4S1 / CS3210"
    assert str(tmp_path) not in overview.text + top.text + course.text + tutorials.text


def test_browse_paginates_and_refuses_paths_outside_the_root(tmp_path: Path) -> None:
    world = World(tmp_path)
    for index in range(12):
        world.add_file(f"notes-{index:02}.md", "text")

    first = world.app.handle(event(f"/browse {world.root.id} 1"))
    second = world.app.handle(event(f"/browse {world.root.id} 2"))

    assert first.text.startswith("Page 1 of 2") and "Next" in [action.label for action in first.actions]
    assert "notes-11.md" in second.text
    assert "Open a folder" in world.app.handle(event(f"/browse {world.root.id} 1 ../secrets"))


# -- file cards and reading -----------------------------------------------------

def test_file_card_shows_relative_location_and_folder_button(tmp_path: Path) -> None:
    world = World(tmp_path)
    tutorial = world.add_file("CS3210/Tutorials/tut04.md", "AVX2 registers", "Vector loads")

    card = world.app.handle(event(f"/source {tutorial.id}"))

    assert "Location: Y4S1 / CS3210 / Tutorials / tut04.md" in card.text
    assert "Text: 2 sections" in card.text
    assert [action.label for action in card.actions] == ["Read", "Summarize", "Ask", "Send original", "Folder"]
    assert world.app.handle(event(card.actions[-1].command)).title == "Y4S1 / CS3210 / Tutorials"
    assert str(tmp_path) not in card.text


def test_read_pages_through_sections_and_explains_missing_text(tmp_path: Path) -> None:
    world = World(tmp_path)
    notes = world.add_file("notes.md", "Parallel loops", "Static scheduling")
    empty = world.add_file("slides.pdf")

    first = world.app.handle(event(f"/source_content {notes.id}"))
    assert "Parallel loops" in first.text and "Static scheduling" not in first.text
    following = world.app.handle(event(next(a.command for a in first.actions if a.label == "Next")))
    assert "Static scheduling" in following.text

    recovery = world.app.handle(event(f"/source_content {empty.id}"))
    assert "doesn't mean it's empty" in recovery.text and "steward reextract" in recovery.text


def test_send_original_delivers_the_file(tmp_path: Path) -> None:
    world = World(tmp_path)
    notes = world.add_file("notes.md", "Parallel loops")

    sent = world.app.handle(event(f"/send_source {notes.id}"))

    assert sent.document is not None and sent.document.content == notes.path.read_bytes()


def test_summarize_button_goes_to_the_summarize_flow(tmp_path: Path) -> None:
    world = World(tmp_path)
    notes = world.add_file("notes.md", "Parallel loops")

    summary = world.app.handle(event(f"/summarize_source {notes.id}"))

    assert summary.title == "Summary: notes.md"
    assert world.answers.calls == [("summarize", notes.id)]


def test_replying_to_a_file_card_asks_about_that_file(tmp_path: Path) -> None:
    world = World(tmp_path)
    notes = world.add_file("notes.md", "Parallel loops")
    world.app.handle(event(f"/source {notes.id}"))

    world.app.handle(event("what does it say?", message_id="8", reply_to="card-message"))

    assert world.answers.calls == [("ask", "100", "what does it say?", notes.id)]
    assert world.contexts.get("telegram", "100").kind == "source"


def test_ask_button_takes_the_next_message_as_a_question(tmp_path: Path) -> None:
    world = World(tmp_path)
    notes = world.add_file("notes.md", "Parallel loops")

    prompt = world.app.handle(event(f"/ask_source {notes.id}"))
    world.app.handle(event("what is covered?", message_id="9"))

    assert prompt.title == "Ask about this file"
    assert world.answers.calls == [("ask", "100", "what is covered?", notes.id)]
    # Without a card reply or Ask prompt, plain text is not silently tied to a file.
    assert commands(world.app.handle(event("what is covered?", message_id="10")))[0].startswith("/find")


# -- uploads ---------------------------------------------------------------------

def test_upload_card_saves_with_intended_root_and_note_into_inbox_md(tmp_path: Path) -> None:
    world = World(tmp_path)
    download = tmp_path / "download.pdf"; download.write_bytes(b"pdf")
    upload = replace(event(message_id="20"), attachments=("tut05.pdf",))

    card = world.app.handle_file(upload, download)
    assert [action.label for action in card.actions] == ["Save", "Intended root", "Add note", "Discard"]
    intake_id = card.actions[0].command.split()[1]

    picker = world.app.handle(event(f"/intake_root {intake_id}", message_id="21"))
    world.app.handle(event(picker.actions[0].command, message_id="22"))
    prompt = world.app.handle(event(f"/intake_context {intake_id}", message_id="23"))
    assert prompt.title == "Add note"
    noted = world.app.handle(event("CS3210 week 5", message_id="24"))
    assert "Intended root: Y4S1" in noted.text and "Note: CS3210 week 5" in noted.text

    saved = world.app.handle(event(f"/intake_accept {intake_id}", message_id="25"))

    assert saved.title.endswith("tut05.pdf") and "INBOX.md" in saved.text
    listing = (world.inbox / "INBOX.md").read_text(encoding="utf-8")
    assert "- Intended root: Y4S1" in listing and "- Note: CS3210 week 5" in listing
    inbox = world.app.handle(event("/inbox", message_id="26"))
    assert "tut05.pdf → Y4S1" in inbox.text  # readable name, then intended root


def test_save_as_note_and_discard(tmp_path: Path) -> None:
    world = World(tmp_path)

    card = world.app.handle(event("/note remember the OpenMP deadline", message_id="30"))
    intake_id = card.actions[0].command.split()[1]
    discarded = world.app.handle(event(f"/intake_discard {intake_id}", message_id="31"))

    assert card.title == "Note"
    assert "Nothing was saved" in discarded.text
    assert world.sources.list_all() == []


def test_inbox_lists_only_files_still_waiting(tmp_path: Path) -> None:
    world = World(tmp_path)
    waiting = world.add_file("waiting.md", "x", under=world.inbox)
    filed = world.add_file("filed.md", "x", under=world.inbox)
    filed.path.unlink()

    inbox = world.app.handle(event("/inbox"))

    assert "1 waiting to be filed" in inbox.text and waiting.path.name in inbox.text
    assert filed.path.name not in inbox.text


# -- /find -----------------------------------------------------------------------

def test_find_parses_filters_and_hands_them_to_the_find_flow(tmp_path: Path) -> None:
    world = World(tmp_path)

    world.app.handle(event('/find tut 4 AVX --type pdf --root "y4s1"'))

    assert world.answers.calls == [("find", "100", "tut 4 AVX", FindScope((SourceType.PDF,), "Y4S1"))]
    assert "No folder is named" in world.app.handle(event('/find law --root "Nope"'))
    assert "Unknown type" in world.app.handle(event("/find law --type spreadsheet"))
    assert "unmatched quote" in world.app.handle(event('/find "law'))


def test_find_accepts_flags_as_phone_keyboards_type_them(tmp_path: Path) -> None:
    world = World(tmp_path)
    wanted = ("find", "100", "tut 4 AVX", FindScope((SourceType.PDF,), "Y4S1"))

    for typed in (
        "/find tut 4 AVX \u2014type pdf \u2014root \u201cY4S1\u201d",
        "/find tut 4 AVX \u2013type pdf \u2013root Y4S1",
        "/find tut 4 AVX type:pdf root:y4s1",
    ):
        world.answers.calls.clear()
        world.app.handle(event(typed))
        assert world.answers.calls == [wanted], typed


def test_find_searches_every_root_unless_one_is_named(tmp_path: Path) -> None:
    world = World(tmp_path)

    world.app.handle(event("/find root causes of latency"))

    assert world.answers.calls == [("find", "100", "root causes of latency", FindScope())]


@pytest.mark.parametrize("command", ["/find", "/note"])
def test_commands_without_text_explain_themselves(tmp_path: Path, command: str) -> None:
    assert "followed by" in World(tmp_path).app.handle(event(command))
