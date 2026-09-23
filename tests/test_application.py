from datetime import UTC, datetime
from dataclasses import replace
from pathlib import Path

from steward.answer import AnswerCitation
from steward.app import (
    StewardCaptureApplication,
    StewardDriveImportApplication,
    StewardGmailImportApplication,
    StewardEventApplication,
    StewardQuestionApplication,
    StewardReadApplication,
    StewardProvisionalIntakeApplication,
    StewardToolAgentApplication,
    StewardRootsApplication,
    StewardPrivacyApplication,
    StewardCodexHandoffApplication,
    TEXT_QUESTION_REQUIRED,
)
from steward.action_proposals import ActionProposalRepository
from steward.capture import CaptureResult, InboxCaptureService
from steward.events import IncomingEvent
from steward.activity import ActivityService, ActivityType
from steward.extraction import ExtractionResult, SourceFragment, SourceFragmentRepository
from steward.sources import CodexHandoffService, Source, SourceRepository, SourceType
from steward.sources.inbox_context import SourceInboxContext, SourceInboxContextRepository
from steward.storage import initialize_database
from steward.retrieval import HybridSearchHit, LexicalSearchService, SemanticSearchHit
from steward.presentation import PresentedReply
from steward.intake import ProvisionalIntakeRepository, ProvisionalIntakeService
from steward.roots import SourceRootProfileRepository, SourceRootRepository
from steward.privacy import PrivacyRule, PrivacyService
from steward.reviews import ReviewContextRepository
from steward.telegram import TelegramUpdateDeliveryRepository
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.errors import GraphRecursionError


class FakeGraph:
    def __init__(self) -> None:
        self.inputs: list[dict[str, str]] = []

    def invoke(self, input: dict[str, str], config=None) -> dict[str, str]:
        self.inputs.append(input)
        return {"answer": "A TLB caches address translations. [F1]"}


def make_event(
    *, text: str | None, reply_text: str | None = None, chat_id: str = "100"
) -> IncomingEvent:
    return IncomingEvent(
        id="telegram:42",
        platform="telegram",
        chat_id=chat_id,
        message_id="7",
        reply_to_id=None,
        timestamp=datetime(2026, 9, 7, tzinfo=UTC),
        text=text,
        reply_text=reply_text,
    )


def test_question_application_passes_normalized_text_to_graph() -> None:
    graph = FakeGraph()

    response = StewardQuestionApplication(graph).handle(make_event(text="What is a TLB?"))

    assert graph.inputs == [{"question": "What is a TLB?"}]
    assert response == "A TLB caches address translations. [F1]"


def test_question_application_includes_bounded_explicit_telegram_reply_context() -> None:
    graph = FakeGraph()

    StewardQuestionApplication(graph).handle(
        make_event(text="How does this relate?", reply_text="A TLB caches address translations.")
    )

    assert graph.inputs == [{
        "question": (
            "Reply context (user-supplied): A TLB caches address translations.\n\n"
            "Current question: How does this relate?"
        )
    }]


def test_question_application_does_not_invoke_graph_for_empty_text() -> None:
    graph = FakeGraph()

    response = StewardQuestionApplication(graph).handle(make_event(text="   "))

    assert response == TEXT_QUESTION_REQUIRED
    assert graph.inputs == []


def test_question_application_includes_source_details_for_citations() -> None:
    class CitedGraph:
        def invoke(self, input: dict[str, str], config=None):
            return {
                "answer": "A TLB caches translations. [F1]",
                "citations": (
                    AnswerCitation(
                        key="F1",
                        fragment_id=3,
                        source_path=Path("vault/virtual-memory.md"),
                        heading="TLB",
                        location="lines 4-6",
                    ),
                ),
            }

    response = StewardQuestionApplication(CitedGraph()).handle(
        make_event(text="What is a TLB?")
    )

    assert isinstance(response, PresentedReply)
    assert response.title == "Answer from your saved material"
    assert response.text == (
        "A TLB caches translations. [F1]\n\n"
        "Sources:\n[F1] virtual-memory.md:lines 4-6 [TLB]"
    )


def test_capture_application_requires_explicit_text() -> None:
    class FakeCaptureService:
        def capture_text(self, event):
            raise AssertionError("empty capture must not be persisted")

    assert StewardCaptureApplication(FakeCaptureService()).handle(make_event(text="/save")) == (
        "Use /save followed by the text you want Steward to keep."
    )


def test_event_application_routes_a_question() -> None:
    graph = FakeGraph()
    class Capture:
        def handle(self, event): raise AssertionError("question must not capture")
    response = StewardEventApplication(StewardQuestionApplication(graph), Capture()).handle(
        make_event(text="What is a TLB?")
    )
    assert "TLB" in response


def test_help_explains_read_boundaries_and_reviewable_writes() -> None:
    help_text = StewardReadApplication.help_text()

    for command in ("/sources", "/inbox", "/source ID", "/hybrid_search", "/roots", "/moves",
                    "/codex_handoff", "/privacy SOURCE_ID"):
        assert command in help_text
    assert "Codex organises saved Inbox files" in help_text
    for removed in ("/workspaces", "/tasks", "/research", "/calendar", "/records"):
        assert removed not in help_text


def test_telegram_exposes_local_semantic_and_hybrid_search_without_a_model_call(tmp_path: Path) -> None:
    database_path = tmp_path / "steward.db"
    initialize_database(database_path)
    inbox = tmp_path / "vault" / "inbox"; inbox.mkdir(parents=True)
    source_path = inbox / "memory.md"; source_path.write_text("TLB notes", encoding="utf-8")
    now = datetime(2026, 9, 8, tzinfo=UTC)
    sources = SourceRepository(database_path)
    source = sources.add(Source(None, source_path, "a" * 64, SourceType.MARKDOWN, 9, now, now, now))
    fragments = SourceFragmentRepository(database_path)
    fragment = fragments.replace_for_source(ExtractionResult(
        source.id or 0,
        (SourceFragment(None, source.id or 0, "TLB", 0, "A TLB caches translations.", "lines 1-1"),),
    ))[0]

    class Semantic:
        def search(self, query: str, *, limit: int):
            assert query == "CPU translation cache" and limit == 5
            return (SemanticSearchHit(source, fragment, 0.91),)

    class Hybrid:
        def search(self, query: str, *, limit: int):
            assert query == "CPU translation cache" and limit == 5
            return (HybridSearchHit(source, fragment, 0.031, None, 0.91),)

    reads = StewardReadApplication(
        sources, fragments, LexicalSearchService(sources, fragments),
        ActivityService(database_path), inbox,
        semantic_search=Semantic(), hybrid_retriever=Hybrid(),
    )

    semantic = reads.handle_command(make_event(text="/semantic_search CPU translation cache"))
    hybrid = reads.handle_command(make_event(text="/hybrid_search CPU translation cache"))

    assert isinstance(semantic, PresentedReply) and isinstance(hybrid, PresentedReply)
    assert semantic.title == "Semantic search results" and "memory.md" in semantic.text
    assert "Similarity: 0.91" in semantic.text and str(tmp_path) not in semantic.text
    assert hybrid.title == "Hybrid search results" and "Match: 0.03" in hybrid.text


def test_telegram_semantic_search_has_a_safe_unavailable_response(tmp_path: Path) -> None:
    database_path = tmp_path / "steward.db"; initialize_database(database_path)

    class UnavailableSemantic:
        def search(self, query: str, *, limit: int):
            raise RuntimeError("C:/private/embedding-cache is missing")

    reads = StewardReadApplication(
        SourceRepository(database_path), SourceFragmentRepository(database_path),
        LexicalSearchService(SourceRepository(database_path), SourceFragmentRepository(database_path)),
        ActivityService(database_path), tmp_path,
        semantic_search=UnavailableSemantic(),
    )

    response = reads.handle_command(make_event(text="/semantic_search cache"))

    assert response == "Semantic search is temporarily unavailable. Verify the local embedding model and derived index."
    assert "C:/private" not in response


def test_activity_hides_local_directory_structure_from_telegram(tmp_path: Path) -> None:
    database_path = tmp_path / "steward.db"
    initialize_database(database_path)
    inbox = tmp_path / "private" / "vault" / "inbox"
    inbox.mkdir(parents=True)
    activity = ActivityService(database_path)
    activity.record(
        ActivityType.SOURCE_CAPTURED,
        object_id="1",
        details=str(inbox / "private-note.md"),
    )
    reads = StewardReadApplication(
        SourceRepository(database_path),
        SourceFragmentRepository(database_path),
        LexicalSearchService(SourceRepository(database_path), SourceFragmentRepository(database_path)),
        activity,
        inbox,
    )

    response = reads.activity("")

    assert isinstance(response, PresentedReply)
    assert "private-note.md" in response.text
    assert str(tmp_path) not in response.text
    assert "local file:" in response.text


def test_telegram_activity_cards_are_safe_and_reopen_the_exact_event_after_restart(tmp_path: Path) -> None:
    database = tmp_path / "steward.db"; initialize_database(database)
    inbox = tmp_path / "private" / "vault" / "inbox"; inbox.mkdir(parents=True)
    activity = ActivityService(database)
    event = activity.record(ActivityType.SOURCE_CAPTURED, object_id="8", details=str(inbox / "private-note.md"))
    contexts = ReviewContextRepository(database)

    def reads() -> StewardReadApplication:
        return StewardReadApplication(
            SourceRepository(database), SourceFragmentRepository(database),
            LexicalSearchService(SourceRepository(database), SourceFragmentRepository(database)),
            ActivityService(database), inbox, contexts=contexts,
        )

    first = StewardEventApplication(
        StewardQuestionApplication(FakeGraph()), StewardCaptureApplication(type("Capture", (), {})()),
        read_application=reads(),
    )
    listing = first.handle(make_event(text="/activity"))
    assert isinstance(listing, PresentedReply)
    assert listing.actions[0].command == f"/activity_event {event.id}"
    card = first.handle(make_event(text=listing.actions[0].command))
    assert isinstance(card, PresentedReply)
    assert card.reference == ("activity", event.id)
    assert "private-note.md" in card.text and str(tmp_path) not in card.text
    assert "does not repeat the action" in card.text

    restarted = StewardEventApplication(
        StewardQuestionApplication(FakeGraph()), StewardCaptureApplication(type("Capture", (), {})()),
        read_application=reads(),
    )
    reopened = restarted.handle(make_event(text="show that activity"))
    assert isinstance(reopened, PresentedReply)
    assert reopened.reference == ("activity", event.id)


def test_metrics_reports_aggregate_activity_without_event_details(tmp_path: Path) -> None:
    database = tmp_path / "steward.db"; initialize_database(database)
    inbox = tmp_path / "vault" / "inbox"; inbox.mkdir(parents=True)
    activity = ActivityService(database)
    activity.record(ActivityType.ACTION_REJECTED, details="C:/private/secret-note.md")
    activity.record(ActivityType.ACTION_REJECTED, details="another private detail")
    reads = StewardReadApplication(
        SourceRepository(database), SourceFragmentRepository(database),
        LexicalSearchService(SourceRepository(database), SourceFragmentRepository(database)),
        activity, inbox,
    )

    response = reads.handle_command(make_event(text="/metrics"))

    assert response == "Local activity metrics:\naction_rejected: 2"
    assert "C:/private" not in response and "another private detail" not in response


def test_status_reports_review_and_delivery_health_without_message_content(tmp_path: Path) -> None:
    database = tmp_path / "steward.db"; initialize_database(database)
    inbox = tmp_path / "vault" / "inbox"; inbox.mkdir(parents=True)
    deliveries = TelegramUpdateDeliveryRepository(database)
    assert deliveries.claim("telegram:health")
    reads = StewardReadApplication(
        SourceRepository(database), SourceFragmentRepository(database),
        LexicalSearchService(SourceRepository(database), SourceFragmentRepository(database)),
        ActivityService(database), inbox, deliveries,
    )

    response = reads.status()

    assert "Telegram delivery: 1 processing, 0 dead letters" in response


def test_status_renders_only_injected_safe_runtime_labels(tmp_path: Path) -> None:
    database = tmp_path / "steward.db"; initialize_database(database)
    reads = StewardReadApplication(
        SourceRepository(database), SourceFragmentRepository(database),
        LexicalSearchService(SourceRepository(database), SourceFragmentRepository(database)),
        ActivityService(database), tmp_path,
        runtime_status=lambda: (
            "Operational database: available",
            "Conversation checkpoints: unavailable",
            "Authorized roots: 1 available, 0 missing, 0 disabled",
            "Model provider: local configured",
        ),
    )

    response = reads.status()

    assert "Operational database: available" in response
    assert "Conversation checkpoints: unavailable" in response
    assert "Model provider: local configured" in response
    assert str(tmp_path) not in response


def test_status_hides_runtime_callback_failures(tmp_path: Path) -> None:
    database = tmp_path / "steward.db"; initialize_database(database)

    def fail() -> tuple[str, ...]:
        raise RuntimeError(f"C:/private/{tmp_path.name}/database")

    reads = StewardReadApplication(
        SourceRepository(database), SourceFragmentRepository(database),
        LexicalSearchService(SourceRepository(database), SourceFragmentRepository(database)),
        ActivityService(database), tmp_path, runtime_status=fail,
    )

    response = reads.status()

    assert "Local runtime health: temporarily unavailable" in response
    assert "C:/private" not in response


def test_event_application_gives_helpful_unknown_response() -> None:
    response = StewardEventApplication(
        StewardQuestionApplication(FakeGraph()), StewardCaptureApplication(type("Capture", (), {})())
    ).handle(make_event(text="please do a mysterious thing"))

    assert "Try /help" in response


def test_agent_command_uses_a_persistent_chat_scoped_tool_thread() -> None:
    class ToolGraph:
        def __init__(self) -> None:
            self.input = None
            self.config = None

        def invoke(self, input, config):
            self.input = input
            self.config = config
            return {"messages": [type("Final", (), {"content": "Found local evidence."})()]}

    graph = ToolGraph()
    application = StewardEventApplication(
        StewardQuestionApplication(FakeGraph()),
        StewardCaptureApplication(type("Capture", (), {})()),
        tool_agent_application=StewardToolAgentApplication(graph),
    )

    response = application.handle(make_event(text="/agent what do I know about OpenMP"))

    assert response == "Found local evidence."
    assert graph.config["configurable"]["thread_id"] == "tool-agent:telegram:100"
    assert graph.input["messages"][1].content == "what do I know about OpenMP"


def test_explicit_agent_command_uses_the_read_only_tool_agent() -> None:
    class ToolGraph:
        def __init__(self) -> None:
            self.calls = 0

        def invoke(self, input, config):
            self.calls += 1
            assert input["messages"][1].content == "What do I know about OpenMP?"
            assert config["configurable"]["thread_id"] == "tool-agent:telegram:100"
            return {"messages": [type("Final", (), {"content": "Found local OpenMP notes."})()]}

    graph = ToolGraph()
    application = StewardEventApplication(
        StewardQuestionApplication(FakeGraph()),
        StewardCaptureApplication(type("Capture", (), {})()),
        tool_agent_application=StewardToolAgentApplication(graph),
    )

    assert application.handle(make_event(text="/agent What do I know about OpenMP?")) == "Found local OpenMP notes."
    assert graph.calls == 1
    assert application.handle(make_event(text="What do I know about OpenMP?")) == "A TLB caches address translations. [F1]"
    assert graph.calls == 1


def test_source_centric_events_prefer_the_grounded_question_graph_over_tool_choice() -> None:
    class GroundedGraph:
        def __init__(self) -> None:
            self.calls = 0

        def invoke(self, input, _config):
            self.calls += 1
            assert input == {"question": "What do I know about OpenMP?"}
            return {"answer": "Grounded local answer."}

    class ToolAgentMustNotRun:
        def handle_command(self, _event):
            return None

    graph = GroundedGraph()
    application = StewardEventApplication(
        StewardQuestionApplication(graph),
        StewardCaptureApplication(type("Capture", (), {})()),
        tool_agent_application=ToolAgentMustNotRun(),
    )

    assert application.handle(make_event(text="What do I know about OpenMP?")) == "Grounded local answer."
    assert graph.calls == 1


def test_tool_agent_uses_an_explicit_telegram_reply_as_bounded_context() -> None:
    class ToolGraph:
        def invoke(self, input, _config):
            assert input["messages"][1].content == (
                "Reply context (user-supplied): My CS3210 notes use OpenMP.\n\n"
                "Current question: Explain this further."
            )
            return {"messages": [type("Final", (), {"content": "Explained."})()]}

    response = StewardToolAgentApplication(ToolGraph()).handle_command(
        make_event(text="/agent Explain this further.", reply_text="My CS3210 notes use OpenMP.")
    )

    assert response == "Explained."


def test_tool_agent_labels_generated_and_tool_backed_answers() -> None:
    class GeneratedGraph:
        def invoke(self, _input, _config):
            return {"messages": [HumanMessage("what is a TLB?"), AIMessage("A TLB is a translation cache.")]}

    class LocalToolGraph:
        def invoke(self, _input, _config):
            return {
                "messages": [
                    HumanMessage("what do I know about TLBs?"),
                    ToolMessage("[]", tool_call_id="call-1", name="search_sources"),
                    AIMessage("I found your virtual-memory notes."),
                ]
            }

    generated = StewardToolAgentApplication(GeneratedGraph()).handle_command(make_event(text="/agent what is a TLB?"))
    sourced = StewardToolAgentApplication(LocalToolGraph()).handle_command(make_event(text="/agent what do I know about TLBs?"))

    assert isinstance(generated, PresentedReply)
    assert generated.title == "Generated answer"
    assert "did not search your saved material" in generated.text
    assert isinstance(sourced, PresentedReply)
    assert sourced.title == "Answer using saved material"
    assert "searched your local saved material" in sourced.text


def test_agent_command_turns_a_graph_recursion_limit_into_a_safe_reply() -> None:
    class LoopingToolGraph:
        def invoke(self, input, config):
            raise GraphRecursionError("tool loop did not terminate")

    application = StewardEventApplication(
        StewardQuestionApplication(FakeGraph()),
        StewardCaptureApplication(type("Capture", (), {})()),
        tool_agent_application=StewardToolAgentApplication(LoopingToolGraph()),
    )

    response = application.handle(make_event(text="/agent find all CS3210 notes"))

    assert response == (
        "I stopped the tool workflow before it could loop further. "
        "No write was performed; please narrow the request and try again."
    )


def test_agent_command_hides_unexpected_tool_workflow_diagnostics() -> None:
    class BrokenToolGraph:
        def invoke(self, input, config):
            raise RuntimeError("C:/private/provider-token failed: raw upstream payload")

    application = StewardEventApplication(
        StewardQuestionApplication(FakeGraph()),
        StewardCaptureApplication(type("Capture", (), {})()),
        tool_agent_application=StewardToolAgentApplication(BrokenToolGraph()),
    )

    response = application.handle(make_event(text="/agent find all CS3210 notes"))

    assert response == (
        "The read-only tool workflow is temporarily unavailable. "
        "No change was made; please retry later or use a narrower question."
    )
    assert "private" not in response
    assert "payload" not in response


def test_roots_command_reports_only_locally_authorized_root_health(tmp_path: Path) -> None:
    database_path = tmp_path / "steward.db"; initialize_database(database_path)
    root_path = tmp_path / "notes"; root_path.mkdir()
    roots = SourceRootRepository(database_path)
    root = roots.add("School", root_path)
    SourceRootProfileRepository(database_path).set(
        root, purpose="School material", authority_tiers=("official", "notes"),
    )
    from steward.sources import ScanResult
    roots.record_successful_scan(root, ScanResult(new=1, updated=2, unchanged=3, missing=4))
    application = StewardEventApplication(
        StewardQuestionApplication(FakeGraph()), StewardCaptureApplication(type("Capture", (), {})()),
        roots_application=StewardRootsApplication(
            roots, contexts=ReviewContextRepository(database_path),
            profiles=SourceRootProfileRepository(database_path),
        ),
    )

    response = application.handle(make_event(text="/roots"))

    assert isinstance(response, PresentedReply)
    assert response.title == "Authorized source roots"
    assert response.text == "Page 1 of 1\n1: School (available)"
    assert response.actions[0].command == "/root 1"
    detail = application.handle(make_event(text="/root 1"))
    assert isinstance(detail, PresentedReply)
    assert detail.title == "School"
    assert "Purpose: School material" in detail.text
    assert "Authority tiers: official · notes" in detail.text
    assert "Last scan outcome: new=1 updated=2 unchanged=3 missing=4" in detail.text
    assert "Root paths and changes remain local-only." in detail.text
    assert detail.reference == ("root", 1)
    reopened = application.handle(make_event(text="show that root"))
    assert isinstance(reopened, PresentedReply)
    assert reopened.title == "School"
    assert reopened.reference == ("root", 1)
    roots.set_enabled("School", False)
    disabled = application.handle(make_event(text="/roots"))
    assert isinstance(disabled, PresentedReply)
    assert disabled.text == "Page 1 of 1\n1: School (disabled)"

    roots.set_enabled("School", True)
    root_path.rmdir()
    missing = application.handle(make_event(text="/roots"))
    assert isinstance(missing, PresentedReply)
    assert "School (missing)" in missing.text
    assert str(root_path) not in missing.text


def test_roots_command_explains_safe_local_onboarding_when_no_roots_exist(tmp_path: Path) -> None:
    database_path = tmp_path / "steward.db"; initialize_database(database_path)
    application = StewardEventApplication(
        StewardQuestionApplication(FakeGraph()), StewardCaptureApplication(type("Capture", (), {})()),
        roots_application=StewardRootsApplication(SourceRootRepository(database_path)),
    )

    response = application.handle(make_event(text="/roots"))

    assert isinstance(response, PresentedReply)
    assert response.title == "Set up a local source root"
    assert 'steward onboard-root "My Notes" "C:\\path\\to\\notes"' in response.text
    assert "original files stay where they are" in response.text
    assert "Telegram cannot choose or browse local folders" in response.text
    assert str(tmp_path) not in response.text
    assert [(action.label, action.command) for action in response.actions] == [("Home", "/home")]


def test_roots_command_paginates_many_authorized_roots_without_paths(tmp_path: Path) -> None:
    database_path = tmp_path / "steward.db"; initialize_database(database_path)
    roots = SourceRootRepository(database_path)
    for index in range(9):
        root_path = tmp_path / f"notes-{index}"; root_path.mkdir()
        roots.add(f"Vault {index + 1}", root_path)
    application = StewardEventApplication(
        StewardQuestionApplication(FakeGraph()), StewardCaptureApplication(type("Capture", (), {})()),
        roots_application=StewardRootsApplication(roots),
    )

    first_page = application.handle(make_event(text="/roots"))
    second_page = application.handle(make_event(text="/roots 2"))

    assert isinstance(first_page, PresentedReply)
    assert first_page.text.startswith("Page 1 of 2\n1: Vault 1 (available)")
    assert "Vault 9" not in first_page.text
    assert [action.label for action in first_page.actions] == [
        "Open 1", "Open 2", "Open 3", "Open 4", "Open 5", "Open 6", "Open 7", "Open 8", "Next", "Home",
    ]
    assert isinstance(second_page, PresentedReply)
    assert second_page.text == "Page 2 of 2\n9: Vault 9 (available)"
    assert [action.command for action in second_page.actions] == ["/root 9", "/roots 1", "/home"]
    assert str(tmp_path) not in first_page.text + second_page.text
    assert application.handle(make_event(text="/roots zero")) == "Use /roots with an optional positive page number."


def test_privacy_commands_change_only_one_known_source_policy(tmp_path: Path) -> None:
    database_path = tmp_path / "steward.db"; initialize_database(database_path)
    now = datetime(2026, 9, 9, tzinfo=UTC)
    path = tmp_path / "note.md"; path.write_text("note", encoding="utf-8")
    sources = SourceRepository(database_path)
    sources.add(Source(None, path, "a" * 64, SourceType.MARKDOWN, 4, now, now, now))
    activity = ActivityService(database_path)
    application = StewardEventApplication(
        StewardQuestionApplication(FakeGraph()), StewardCaptureApplication(type("Capture", (), {})()),
        privacy_application=StewardPrivacyApplication(PrivacyService(database_path), sources, activity),
    )

    changed = application.handle(make_event(text="/set_privacy 1 local_model_only"))
    inspected = application.handle(make_event(text="/privacy 1"))

    assert changed == "Source 1 privacy rule set to local_model_only."
    assert inspected == "Source 1 privacy rule: local_model_only"
    assert activity.list_recent()[0].event_type == ActivityType.SOURCE_PRIVACY_CHANGED
    assert activity.list_recent()[0].object_id == "1"


def test_telegram_privacy_review_is_bound_to_the_originating_chat(tmp_path: Path) -> None:
    database = tmp_path / "steward.db"; initialize_database(database)
    now = datetime(2026, 10, 1, tzinfo=UTC)
    source = SourceRepository(database).add(
        Source(None, tmp_path / "private.md", "e" * 64, SourceType.MARKDOWN, 0, now, now, now)
    )
    activity = ActivityService(database)
    proposals = ActionProposalRepository(database)
    privacy = PrivacyService(database)
    application = StewardEventApplication(
        StewardQuestionApplication(FakeGraph()), StewardCaptureApplication(type("Capture", (), {})()),
        privacy_application=StewardPrivacyApplication(
            privacy, SourceRepository(database), activity, proposals
        ),
    )
    owner = make_event(text=f"/set_privacy {source.id} no_model")
    other = IncomingEvent(
        id="telegram:43", platform="telegram", chat_id="other", message_id="8", reply_to_id=None,
        timestamp=now, text="/approve_action 1",
    )

    proposed = application.handle(owner)
    refused = application.handle(other)
    second_chat = application.handle(
        IncomingEvent(
            id="telegram:44", platform="telegram", chat_id="other", message_id="9", reply_to_id=None,
            timestamp=now, text=f"/set_privacy {source.id} local_model_only",
        )
    )

    assert isinstance(proposed, PresentedReply)
    assert "belongs to another" in refused
    assert isinstance(second_chat, PresentedReply)
    assert second_chat.title == "Privacy change pending"
    assert "local_model_only" not in second_chat.text
    assert privacy.rule_for(source.id or 0) is PrivacyRule.EXTERNAL_ALLOWED
    assert proposals.get(1).status == "pending"
    assert application.handle(make_event(text="/approve_action 1")).title == "Privacy rule applied"
    assert privacy.rule_for(source.id or 0) is PrivacyRule.NO_MODEL

    legacy = proposals.add(
        StewardPrivacyApplication.SET_SOURCE_PRIVACY,
        {"source_id": str(source.id), "rule": PrivacyRule.EXTERNAL_ALLOWED.value},
    )
    assert "missing its chat binding" in application.handle(make_event(text=f"/approve_action {legacy.id}"))
    assert application.handle(make_event(text=f"/reject_action {legacy.id}")) == (
        "Legacy privacy proposal declined. Create a fresh review from the source card."
    )
    assert proposals.get(legacy.id or 0).status == "rejected"


def test_telegram_source_privacy_picker_is_available_from_source_and_model_denial(tmp_path: Path) -> None:
    database_path = tmp_path / "steward.db"; initialize_database(database_path)
    now = datetime(2026, 9, 9, tzinfo=UTC)
    path = tmp_path / "private.md"; path.write_text("private note", encoding="utf-8")
    sources = SourceRepository(database_path)
    source = sources.add(Source(None, path, "c" * 64, SourceType.MARKDOWN, 12, now, now, now))
    fragments = SourceFragmentRepository(database_path)
    fragments.replace_for_source(ExtractionResult(
        source.id or 0,
        (SourceFragment(None, source.id or 0, "Private", 0, "private note", "lines 1-1"),),
    ))
    activity = ActivityService(database_path)
    proposals = ActionProposalRepository(database_path)
    privacy = PrivacyService(database_path)
    privacy.set_rule(source.id or 0, PrivacyRule.NO_MODEL)
    reads = StewardReadApplication(
        sources, fragments, LexicalSearchService(sources, fragments), activity, tmp_path, source_model=object(), source_model_allowed=privacy.permits_external_model,
    )
    application = StewardEventApplication(
        StewardQuestionApplication(FakeGraph()), StewardCaptureApplication(type("Capture", (), {})()),
        read_application=reads,
        privacy_application=StewardPrivacyApplication(privacy, sources, activity, proposals),
    )

    source_card = application.handle(make_event(text=f"/source {source.id}"))
    assert isinstance(source_card, PresentedReply)
    assert ("Privacy", f"/privacy_options {source.id}") in {
        (action.label, action.command) for action in source_card.actions
    }
    denied = application.handle(make_event(text=f"/summarize_source {source.id}"))
    assert isinstance(denied, PresentedReply)
    assert denied.title == "Model access blocked"
    assert denied.actions[1].command == f"/privacy_options {source.id}"

    picker = application.handle(make_event(text=f"/privacy_options {source.id}"))
    assert isinstance(picker, PresentedReply)
    assert picker.title == "Source privacy"
    assert "Current rule: no_model" in picker.text
    assert ("Allow cloud", f"/set_privacy {source.id} external_allowed") in {
        (action.label, action.command) for action in picker.actions
    }
    review = application.handle(make_event(text=f"/set_privacy {source.id} external_allowed"))
    assert isinstance(review, PresentedReply)
    assert review.title == "Review privacy change"
    assert privacy.rule_for(source.id or 0) is PrivacyRule.NO_MODEL


def test_opened_source_can_naturally_propose_a_reviewed_privacy_boundary(tmp_path: Path) -> None:
    database = tmp_path / "steward.db"; initialize_database(database)
    now = datetime(2026, 10, 1, tzinfo=UTC)
    source = SourceRepository(database).add(
        Source(None, tmp_path / "notes.md", "d" * 64, SourceType.MARKDOWN, 0, now, now, now)
    )
    fragments = SourceFragmentRepository(database)
    activity = ActivityService(database)
    proposals = ActionProposalRepository(database)
    contexts = ReviewContextRepository(database)
    privacy = PrivacyService(database)
    reads = StewardReadApplication(
        SourceRepository(database), fragments,
        LexicalSearchService(SourceRepository(database), fragments), activity, tmp_path, contexts=contexts,
    )
    application = StewardEventApplication(
        StewardQuestionApplication(FakeGraph()), StewardCaptureApplication(type("Capture", (), {})()),
        read_application=reads,
        privacy_application=StewardPrivacyApplication(
            privacy, SourceRepository(database), activity, proposals, contexts=contexts
        ),
    )

    opened = application.handle(make_event(text=f"/source {source.id}"))
    review = application.handle(make_event(text="keep this local"))

    assert isinstance(opened, PresentedReply)
    assert isinstance(review, PresentedReply)
    assert review.title == "Review privacy change"
    assert "local_model_only" in review.text
    assert privacy.rule_for(source.id or 0) is PrivacyRule.EXTERNAL_ALLOWED
    assert proposals.get(1).action_type == StewardPrivacyApplication.SET_SOURCE_PRIVACY

    accepted = application.handle(make_event(text="/approve_action 1"))

    assert isinstance(accepted, PresentedReply)
    assert accepted.title == "Privacy rule applied"
    assert privacy.rule_for(source.id or 0) is PrivacyRule.LOCAL_MODEL_ONLY


def test_source_pagination_exposes_only_bounded_follow_up_commands(tmp_path: Path) -> None:
    database_path = tmp_path / "steward.db"
    initialize_database(database_path)
    sources = SourceRepository(database_path)
    now = datetime(2026, 9, 8, tzinfo=UTC)
    for identifier in range(11):
        path = tmp_path / f"note-{identifier}.md"
        path.write_text("note", encoding="utf-8")
        sources.add(Source(None, path, f"{identifier:064x}", SourceType.MARKDOWN, 4, now, now, now))
    fragments = SourceFragmentRepository(database_path)
    reads = StewardReadApplication(
        sources, fragments, LexicalSearchService(sources, fragments), ActivityService(database_path), tmp_path / "inbox"
    )

    response = reads.sources(1)

    assert isinstance(response, PresentedReply)
    assert response.actions[0].label == "Open 1"
    assert response.actions[-1].label == "Next"
    assert response.actions[-1].command == "/sources 2"


def test_explicit_casual_capture_prefixes_stage_text_without_auto_saving() -> None:
    assert StewardProvisionalIntakeApplication.should_propose_text(make_event(text="remember buy milk"))
    assert StewardProvisionalIntakeApplication.should_propose_text(make_event(text="thought improve task routing"))
    assert StewardProvisionalIntakeApplication.should_propose_text(make_event(text="note review CS3210 chapter 4"))
    assert StewardProvisionalIntakeApplication.should_propose_text(make_event(text="Hotel reservation confirmation: A1B2C3"))
    assert StewardProvisionalIntakeApplication.should_propose_text(make_event(text="Passport renewal appointment at 9am"))
    assert StewardProvisionalIntakeApplication.should_propose_text(make_event(text="Warranty certificate for my laptop"))
    assert not StewardProvisionalIntakeApplication.should_propose_text(make_event(text="remembering this is useful"))


def test_attachment_is_provisional_unless_its_caption_uses_save(tmp_path: Path) -> None:
    database_path = tmp_path / "steward.db"
    initialize_database(database_path)
    sources = SourceRepository(database_path)
    activity = ActivityService(database_path)
    capture_service = InboxCaptureService(
        tmp_path / "vault" / "inbox", sources, SourceFragmentRepository(database_path), activity
    )
    capture = StewardCaptureApplication(capture_service)
    provisional = StewardProvisionalIntakeApplication(
        ProvisionalIntakeService(
            tmp_path / ".steward" / "cache" / "intake",
            ProvisionalIntakeRepository(database_path),
            capture_service,
            activity,
            PrivacyService(database_path),
        )
    )
    application = StewardEventApplication(
        StewardQuestionApplication(FakeGraph()), capture, provisional_intake_application=provisional
    )
    original = tmp_path / "notes.pdf"
    original.write_bytes(b"pdf")
    attachment_event = IncomingEvent(
        "telegram:attachment", "telegram", "100", "12", None,
        datetime(2026, 9, 9, tzinfo=UTC), None, ("notes.pdf",),
    )

    proposed = application.handle_file(attachment_event, original)

    assert isinstance(proposed, PresentedReply)
    assert any(action.command == "/intake_analysis 1 external" for action in proposed.actions)
    assert sources.list_all() == []
    selected = application.handle(
        IncomingEvent(
            "telegram:analysis", "telegram", "100", "13", None,
            datetime(2026, 9, 9, tzinfo=UTC), "/intake_analysis 1 external",
        )
    )
    assert isinstance(selected, PresentedReply)
    assert "external" in selected.text
    accepted = application.handle(
        IncomingEvent(
            "telegram:accept", "telegram", "100", "13", None,
            datetime(2026, 9, 9, tzinfo=UTC), "/intake_accept 1",
        )
    )
    assert "Saved to Inbox" in accepted
    assert len(sources.list_all()) == 1
    assert PrivacyService(database_path).rule_for(1) is PrivacyRule.EXTERNAL_ALLOWED


def test_provisional_intake_context_command_updates_without_saving(tmp_path: Path) -> None:
    database_path = tmp_path / "steward.db"
    initialize_database(database_path)
    sources = SourceRepository(database_path)
    activity = ActivityService(database_path)
    capture_service = InboxCaptureService(tmp_path / "vault" / "inbox", sources, activity_service=activity)
    provisional = StewardProvisionalIntakeApplication(
        ProvisionalIntakeService(
            tmp_path / ".steward" / "cache" / "intake", ProvisionalIntakeRepository(database_path),
            capture_service, activity, PrivacyService(database_path),
        )
    )
    original = tmp_path / "notes.pdf"
    original.write_bytes(b"pdf")
    event = IncomingEvent(
        "telegram:attachment", "telegram", "100", "12", None,
        datetime(2026, 9, 9, tzinfo=UTC), None, ("notes.pdf",),
    )
    provisional.begin_file(event, original)

    updated = provisional.handle_command(
        IncomingEvent(
            "telegram:context", "telegram", "100", "13", None,
            datetime(2026, 9, 9, tzinfo=UTC), "/intake_context 1 CS3210 assignment",
        )
    )

    assert isinstance(updated, PresentedReply)
    assert "CS3210 assignment" in updated.text
    assert sources.list_all() == []


def test_provisional_intake_collects_context_from_an_ordinary_followup(tmp_path: Path) -> None:
    database_path = tmp_path / "steward.db"
    initialize_database(database_path)
    sources = SourceRepository(database_path)
    activity = ActivityService(database_path)
    capture_service = InboxCaptureService(tmp_path / "vault" / "inbox", sources, activity_service=activity)
    provisional = StewardProvisionalIntakeApplication(
        ProvisionalIntakeService(
            tmp_path / ".steward" / "cache" / "intake", ProvisionalIntakeRepository(database_path),
            capture_service, activity, PrivacyService(database_path),
        ),
        contexts=ReviewContextRepository(database_path),
    )
    application = StewardEventApplication(
        StewardQuestionApplication(FakeGraph()), StewardCaptureApplication(capture_service),
        provisional_intake_application=provisional,
    )
    original = tmp_path / "notes.pdf"
    original.write_bytes(b"pdf")
    upload = IncomingEvent(
        "telegram:attachment", "telegram", "100", "12", None,
        datetime(2026, 9, 9, tzinfo=UTC), None, ("notes.pdf",),
    )
    card = application.handle_file(upload, original)

    prompt = application.handle(make_event(text="/intake_context 1"))
    revised = application.handle(make_event(text="This is for my CS3210 OpenMP assignment."))

    assert isinstance(card, PresentedReply)
    assert "Provisional intake" not in card.text
    assert isinstance(prompt, PresentedReply)
    assert prompt.title == "Add context"
    assert prompt.reference == ("intake_context", 1)
    assert isinstance(revised, PresentedReply)
    assert "CS3210 OpenMP assignment" in revised.text
    assert sources.list_all() == []


def test_explicit_second_note_is_not_consumed_as_context_for_an_older_intake(tmp_path: Path) -> None:
    database_path = tmp_path / "steward.db"
    initialize_database(database_path)
    activity = ActivityService(database_path)
    capture_service = InboxCaptureService(
        tmp_path / "vault" / "inbox", SourceRepository(database_path), activity_service=activity
    )
    service = ProvisionalIntakeService(
        tmp_path / ".steward" / "cache" / "intake", ProvisionalIntakeRepository(database_path),
        capture_service, activity, PrivacyService(database_path),
    )
    provisional = StewardProvisionalIntakeApplication(
        service, contexts=ReviewContextRepository(database_path),
    )
    application = StewardEventApplication(
        StewardQuestionApplication(FakeGraph()), StewardCaptureApplication(capture_service),
        provisional_intake_application=provisional,
    )

    first = application.handle(replace(make_event(text="note: First CS3210 test item"), message_id="41"))
    prompt = application.handle(replace(make_event(text="/intake_context 1"), message_id="42"))
    second = application.handle(replace(make_event(text="note: Second CS4226 test item"), id="telegram:43", message_id="43"))
    revised = application.handle(replace(make_event(text="This belongs to CS3210 parallel computing"), id="telegram:44", message_id="44"))

    assert isinstance(first, PresentedReply)
    assert isinstance(prompt, PresentedReply) and prompt.title == "Add context"
    assert isinstance(second, PresentedReply)
    assert second.reference == ("intake", 2)
    assert isinstance(revised, PresentedReply)
    assert revised.reference == ("intake", 1)
    assert "CS3210 parallel computing" in revised.text
    intakes = ProvisionalIntakeRepository(database_path).list_all()
    assert len(intakes) == 2
    assert "CS3210 parallel computing" in intakes[0].summary
    assert "CS3210 parallel computing" not in intakes[1].summary


def test_provisional_intake_context_followup_survives_a_restart(tmp_path: Path) -> None:
    database_path = tmp_path / "steward.db"
    initialize_database(database_path)
    sources = SourceRepository(database_path)
    activity = ActivityService(database_path)
    capture_service = InboxCaptureService(tmp_path / "vault" / "inbox", sources, activity_service=activity)

    def build_application() -> StewardEventApplication:
        provisional = StewardProvisionalIntakeApplication(
            ProvisionalIntakeService(
                tmp_path / ".steward" / "cache" / "intake", ProvisionalIntakeRepository(database_path),
                capture_service, activity, PrivacyService(database_path),
            ),
            contexts=ReviewContextRepository(database_path),
        )
        return StewardEventApplication(
            StewardQuestionApplication(FakeGraph()), StewardCaptureApplication(capture_service),
            provisional_intake_application=provisional,
        )

    original = tmp_path / "notes.pdf"
    original.write_bytes(b"pdf")
    first = build_application()
    first.handle_file(
        IncomingEvent(
            "telegram:attachment", "telegram", "100", "12", None,
            datetime(2026, 9, 9, tzinfo=UTC), None, ("notes.pdf",),
        ),
        original,
    )
    assert isinstance(first.handle(make_event(text="/intake_context 1")), PresentedReply)

    restarted = build_application()
    revised = restarted.handle(make_event(text="This belongs to my CS4226 network notes."))

    assert isinstance(revised, PresentedReply)
    assert "CS4226 network notes" in revised.text
    assert sources.list_all() == []


def test_replying_save_to_a_staged_attachment_accepts_that_exact_intake(tmp_path: Path) -> None:
    database_path = tmp_path / "steward.db"
    initialize_database(database_path)
    sources = SourceRepository(database_path)
    activity = ActivityService(database_path)
    capture_service = InboxCaptureService(tmp_path / "vault" / "inbox", sources, activity_service=activity)
    application = StewardEventApplication(
        StewardQuestionApplication(FakeGraph()),
        StewardCaptureApplication(capture_service),
        provisional_intake_application=StewardProvisionalIntakeApplication(
            ProvisionalIntakeService(
                tmp_path / ".steward" / "cache" / "intake", ProvisionalIntakeRepository(database_path),
                capture_service, activity, PrivacyService(database_path),
            )
        ),
    )
    original = tmp_path / "lecture-notes.md"
    original.write_text("# OpenMP\n", encoding="utf-8")
    upload = IncomingEvent(
        "telegram:attachment", "telegram", "100", "12", None,
        datetime(2026, 9, 9, tzinfo=UTC), None, ("lecture-notes.md",),
    )
    application.handle_file(upload, original)

    response = application.handle(
        IncomingEvent(
            "telegram:save-reply", "telegram", "100", "13", "12",
            datetime(2026, 9, 9, tzinfo=UTC), "/save",
        )
    )

    assert "Saved to Inbox" in response
    assert [source.path.name for source in sources.list_all()] == ["telegram-100-12-lecture-notes.md"]
    intake = ProvisionalIntakeRepository(database_path).get(1)
    assert intake is not None and intake.status == "accepted"


def test_reply_save_never_accepts_an_intake_from_another_chat(tmp_path: Path) -> None:
    database_path = tmp_path / "steward.db"
    initialize_database(database_path)
    sources = SourceRepository(database_path)
    activity = ActivityService(database_path)
    capture_service = InboxCaptureService(tmp_path / "vault" / "inbox", sources, activity_service=activity)
    application = StewardEventApplication(
        StewardQuestionApplication(FakeGraph()),
        StewardCaptureApplication(capture_service),
        provisional_intake_application=StewardProvisionalIntakeApplication(
            ProvisionalIntakeService(
                tmp_path / ".steward" / "cache" / "intake", ProvisionalIntakeRepository(database_path),
                capture_service, activity, PrivacyService(database_path),
            )
        ),
    )
    original = tmp_path / "private.md"
    original.write_text("private", encoding="utf-8")
    application.handle_file(
        IncomingEvent(
            "telegram:attachment", "telegram", "100", "12", None,
            datetime(2026, 9, 9, tzinfo=UTC), None, ("private.md",),
        ),
        original,
    )

    response = application.handle(
        IncomingEvent(
            "telegram:save-reply", "telegram", "200", "13", "12",
            datetime(2026, 9, 9, tzinfo=UTC), "/save",
        )
    )

    assert "Use /save followed by the text" in response
    assert sources.list_all() == []
    intake = ProvisionalIntakeRepository(database_path).get(1)
    assert intake is not None and intake.status == "pending"


def test_short_personal_record_text_is_staged_without_being_saved(tmp_path: Path) -> None:
    database_path = tmp_path / "steward.db"
    initialize_database(database_path)
    sources = SourceRepository(database_path)
    activity = ActivityService(database_path)
    capture_service = InboxCaptureService(tmp_path / "vault" / "inbox", sources, activity_service=activity)
    application = StewardEventApplication(
        StewardQuestionApplication(FakeGraph()), StewardCaptureApplication(capture_service),
        provisional_intake_application=StewardProvisionalIntakeApplication(
            ProvisionalIntakeService(
                tmp_path / ".steward" / "cache" / "intake", ProvisionalIntakeRepository(database_path),
                capture_service, activity, PrivacyService(database_path),
            )
        ),
    )

    response = application.handle(make_event(text="My flight to Tokyo leaves on Friday evening."))

    assert isinstance(response, PresentedReply)
    assert response.title == "Review message.md"
    assert "Type: record" in response.text
    assert sources.list_all() == []


def test_discarded_intake_removes_its_staged_copy_and_clears_its_context(tmp_path: Path) -> None:
    database = tmp_path / "steward.db"; initialize_database(database)
    contexts = ReviewContextRepository(database)
    intake_repository = ProvisionalIntakeRepository(database)
    service = ProvisionalIntakeService(
        tmp_path / "staging", intake_repository,
        InboxCaptureService(tmp_path / "vault" / "inbox", SourceRepository(database)),
        ActivityService(database), PrivacyService(database),
    )
    application = StewardProvisionalIntakeApplication(service, contexts=contexts)
    staged = service.stage_text(make_event(text="note: do not keep this temporary detail"))
    contexts.set("telegram", "100", "intake_context", staged.id or 0)

    discarded = application.handle_command(make_event(text=f"/intake_discard {staged.id}"))

    assert isinstance(discarded, PresentedReply)
    assert discarded.title == "Staged item discarded"
    assert "nothing was saved to Inbox" in discarded.text
    assert [action.command for action in discarded.actions] == ["/inbox", "/home"]
    assert intake_repository.get(staged.id or 0).status == "discarded"
    assert not staged.staged_path.exists()
    assert contexts.get("telegram", "100") is None


def test_shared_link_is_staged_without_fetching_or_saving(tmp_path: Path) -> None:
    database_path = tmp_path / "steward.db"
    initialize_database(database_path)
    sources = SourceRepository(database_path)
    activity = ActivityService(database_path)
    capture_service = InboxCaptureService(tmp_path / "vault" / "inbox", sources, activity_service=activity)
    application = StewardEventApplication(
        StewardQuestionApplication(FakeGraph()), StewardCaptureApplication(capture_service),
        provisional_intake_application=StewardProvisionalIntakeApplication(
            ProvisionalIntakeService(
                tmp_path / ".steward" / "cache" / "intake", ProvisionalIntakeRepository(database_path),
                capture_service, activity, PrivacyService(database_path),
            )
        ),
    )

    response = application.handle(make_event(text="https://example.test/useful-paper"))

    assert isinstance(response, PresentedReply)
    assert "Type: reference" in response.text
    assert "No content was sent to a model" in response.text
    assert sources.list_all() == []


def test_provisional_intake_failure_does_not_disclose_a_local_staging_path() -> None:
    class UnavailableService:
        def accept(self, _intake_id: int, _event: IncomingEvent) -> CaptureResult:
            raise OSError("C:/private/staging/telegram-100-1.pdf is locked")

    response = StewardProvisionalIntakeApplication(UnavailableService()).handle_command(  # type: ignore[arg-type]
        make_event(text="/intake_accept 1")
    )

    assert response == (
        "Could not update this provisional intake because local staging is temporarily unavailable. Try again later."
    )
    assert "C:/private" not in response


def test_event_application_routes_downloaded_document_to_capture_service(tmp_path: Path) -> None:
    class FileCapture:
        def __init__(self) -> None:
            self.received: tuple[IncomingEvent, Path] | None = None

        def capture_file(self, event: IncomingEvent, path: Path) -> CaptureResult:
            self.received = (event, path)
            return CaptureResult(source=type("Source", (), {"path": Path("vault/inbox/note.pdf")})(), duplicate=False)

    capture = FileCapture()
    application = StewardEventApplication(StewardQuestionApplication(FakeGraph()), StewardCaptureApplication(capture))
    document = tmp_path / "note.pdf"
    document.write_bytes(b"pdf")

    response = application.handle_file(make_event(text=None), document)

    assert response == "Saved to Inbox: note.pdf"
    assert capture.received == (make_event(text=None), document)


def test_telegram_can_explicitly_import_one_drive_file() -> None:
    class Importer:
        def __init__(self) -> None:
            self.file_ids: list[str] = []

        def import_file(self, file_id: str) -> CaptureResult:
            self.file_ids.append(file_id)
            return CaptureResult(
                type("Source", (), {"id": 42, "path": Path("vault/inbox/drive-import-file-42-note.pdf")})(),
                False,
            )

    importer = Importer()
    application = StewardEventApplication(
        StewardQuestionApplication(FakeGraph()),
        StewardCaptureApplication(type("Capture", (), {})()),
        drive_import_application=StewardDriveImportApplication(importer),
    )

    response = application.handle(make_event(text="/drive_import file-42"))

    assert importer.file_ids == ["file-42"]
    assert isinstance(response, PresentedReply)
    assert response.title == "drive-import-file-42-note.pdf"
    assert "Imported from Drive to Inbox" in response.text
    assert [action.command for action in response.actions] == [
        "/source_content 42", "/source 42",
    ]


def test_telegram_drive_import_requires_an_explicit_single_file_id() -> None:
    application = StewardDriveImportApplication(None)

    assert application.handle_command(make_event(text="/drive_import")) == (
        "Use /drive_import followed by one Google Drive file ID."
    )
    assert application.handle_command(make_event(text="/drive_import first second")) == (
        "Use /drive_import followed by one Google Drive file ID."
    )
    assert application.handle_command(make_event(text="/drive_import file-42")) == (
        "Drive import is not configured on this Steward process. "
        "Set STEWARD_GOOGLE_CLIENT_SECRETS, authorize Drive, then try again."
    )


def test_telegram_drive_search_offers_explicit_individual_imports() -> None:
    from steward.search_page import SearchPage
    class Importer:
        def search_page(self, query, *, page_token=None):
            assert query == "parallel"
            return SearchPage((type("DriveFile", (), {"id": "file-42", "name": "Parallel Notes.pdf"})(),))

    response = StewardDriveImportApplication(Importer()).handle_command(make_event(text="/drive_search parallel"))

    assert isinstance(response, PresentedReply)
    assert "file-42: Parallel Notes.pdf" in response.text
    assert response.actions[0].command == "/drive_import file-42"


def test_telegram_can_explicitly_import_one_gmail_message() -> None:
    class Importer:
        def import_message(self, message_id: str) -> CaptureResult:
            assert message_id == "mail-42"
            return CaptureResult(type("Source", (), {"id": 43, "path": Path("vault/inbox/gmail-import-mail-42.eml")})(), False)

    application = StewardGmailImportApplication(Importer())

    response = application.handle_command(make_event(text="/gmail_import mail-42"))
    assert isinstance(response, PresentedReply)
    assert response.title == "gmail-import-mail-42.eml"
    assert "Imported from Gmail to Inbox" in response.text
    assert [action.command for action in response.actions] == [
        "/source_content 43", "/source 43",
    ]
    assert application.handle_command(make_event(text="/gmail_import")) == (
        "Use /gmail_import followed by one Gmail message ID."
    )


def test_telegram_external_import_failures_do_not_disclose_local_diagnostics() -> None:
    class DriveImporter:
        def import_file(self, _file_id: str) -> CaptureResult:
            raise OSError("C:/private/google-drive-token.json is unreadable")

        def search_page(self, _query: str, *, page_token=None):
            raise RuntimeError("C:/private/client-secret.json was rejected")

    class GmailImporter:
        def import_message(self, _message_id: str) -> CaptureResult:
            raise OSError("C:/private/gmail-token.json is unreadable")

        def search_page(self, _query: str, *, page_token=None):
            raise RuntimeError("C:/private/client-secret.json was rejected")

    drive = StewardDriveImportApplication(DriveImporter())
    gmail = StewardGmailImportApplication(GmailImporter())

    replies = (
        drive.handle_command(make_event(text="/drive_import file-42")),
        drive.handle_command(make_event(text="/drive_search OpenMP")),
        gmail.handle_command(make_event(text="/gmail_import mail-42")),
        gmail.handle_command(make_event(text="/gmail_search OpenMP")),
    )

    assert all(isinstance(reply, PresentedReply) for reply in replies)
    assert all("C:/private" not in reply.text for reply in replies)
    assert all("Do not send tokens" in reply.text for reply in replies)
    assert [reply.actions[0].command for reply in replies] == [
        "/drive_import file-42", "/drive_search OpenMP", "/gmail_import mail-42", "/gmail_search OpenMP",
    ]


def test_telegram_gmail_search_offers_explicit_individual_imports() -> None:
    from steward.search_page import SearchPage
    class Importer:
        def search_page(self, query, *, page_token=None):
            assert query == "OpenMP"
            return SearchPage((type("GmailMessage", (), {"id": "mail-42", "subject": "OpenMP assignment"})(),))

    response = StewardGmailImportApplication(Importer()).handle_command(make_event(text="/gmail_search OpenMP"))

    assert isinstance(response, PresentedReply)
    assert "mail-42: OpenMP assignment" in response.text
    assert response.actions[0].command == "/gmail_import mail-42"


def test_external_search_continuation_survives_restart_and_does_not_import(tmp_path) -> None:
    from steward.search_page import SearchPage
    from steward.telegram.callbacks import TelegramCallbackRepository

    database = tmp_path / "callbacks.db"
    initialize_database(database)
    query = 'subject:"parallel notes" 日本語'
    cursor = 'opaque/+= "cursor"'
    for prefix, application_type in (("drive", StewardDriveImportApplication), ("gmail", StewardGmailImportApplication)):
        calls = []
        class Importer:
            fail = False
            def search_page(self, search_query, *, page_token=None):
                calls.append((search_query, page_token))
                assert search_query == query
                if self.fail:
                    raise RuntimeError("secret-token at C:/private/token.json")
                if page_token is None:
                    return SearchPage((), cursor)
                assert page_token == cursor
                item = type("Item", (), {"id": "selected-6", "name": "Notes.pdf", "subject": "Notes"})()
                return SearchPage((item,))
            def import_file(self, _identifier):
                raise AssertionError("Browsing must not download")
            import_message = import_file

        first = application_type(Importer()).handle_command(make_event(text=f"/{prefix}_search {query}"))
        assert "No items on this page" in first.text
        more = next(action for action in first.actions if action.label == "More results")
        saved = TelegramCallbackRepository(database).create("test-chat", more.command)
        repository = TelegramCallbackRepository(database)
        assert repository.resolve(saved.token, "another-chat") is None
        restored = repository.resolve(saved.token, "test-chat")
        importer = Importer()
        application = application_type(importer)
        page = application.handle_command(make_event(text=restored.command))
        assert page.actions[0].label == "Import 1"
        assert page.actions[0].command == f"/{prefix}_import selected-6"
        assert not any(action.label == "More results" for action in page.actions)
        assert calls == [(query, None), (query, cursor)]
        importer.fail = True
        failure = application.handle_command(make_event(text=restored.command))
        assert "secret-token" not in failure.text and "C:/private" not in failure.text
        assert failure.actions[0].command == restored.command
        assert failure.actions[-1].command == f"/{prefix}_search {query}"
        before = len(calls)
        for malformed in ('null', '{}', '[1,2]', '["query"]'):
            invalid = application.handle_command(make_event(text=f"/{prefix}_page {malformed}"))
            assert "invalid" in invalid
        assert len(calls) == before


def test_reader_source_card_and_home_expose_only_live_actions(tmp_path: Path) -> None:
    database = tmp_path / "steward.db"
    initialize_database(database)
    now = datetime.now(UTC)
    sources = SourceRepository(database)
    source = sources.add(Source(None, tmp_path / "tlb.md", "a" * 64, SourceType.MARKDOWN, 1, now, now, now))
    fragments = SourceFragmentRepository(database)
    reader = StewardReadApplication(
        sources,
        fragments,
        LexicalSearchService(sources, fragments),
        ActivityService(database),
        tmp_path / "inbox",
    )

    home = reader.handle_command(make_event(text="/home"))
    card = reader.handle_command(make_event(text=f"/source {source.id}"))

    assert isinstance(home, PresentedReply) and home.title == "Steward"
    assert isinstance(card, PresentedReply)
    assert [action.command for action in card.actions] == [
        f"/source_content {source.id}", f"/summarize_source {source.id}", f"/ask_source {source.id}",
        f"/privacy_options {source.id}", f"/codex_handoff {source.id}",
    ]
    assert reader.handle_command(make_event(text="/workspaces")) is None


def test_source_card_shows_saved_inbox_capture_context(tmp_path: Path) -> None:
    database = tmp_path / "steward.db"
    initialize_database(database)
    now = datetime.now(UTC)
    sources = SourceRepository(database)
    source = sources.add(Source(
        None, tmp_path / "vault" / "inbox" / "lecture.md", "b" * 64,
        SourceType.MARKDOWN, 1, now, now, now,
    ))
    contexts = SourceInboxContextRepository(database)
    contexts.set(SourceInboxContext(
        source.id or 0, 7, "Y4S1", "CS3210 lecture notes", "telegram", now,
    ))
    fragments = SourceFragmentRepository(database)
    reader = StewardReadApplication(
        sources, fragments, LexicalSearchService(sources, fragments), ActivityService(database), tmp_path / "vault" / "inbox",
        inbox_contexts=contexts,
    )

    card = reader.source(str(source.id))

    assert isinstance(card, PresentedReply)
    assert "Intended root: Y4S1" in card.text
    assert "Capture context: CS3210 lecture notes" in card.text
    assert "Capture origin: telegram" in card.text
    assert "Location: Inbox / lecture.md" in card.text
    assert "Extraction: no extracted text available" in card.text


def test_source_card_uses_a_safe_root_relative_location(tmp_path: Path) -> None:
    database = tmp_path / "steward.db"
    initialize_database(database)
    root_path = tmp_path / "Course"; root_path.mkdir()
    roots = SourceRootRepository(database)
    roots.add("CS3210", root_path)
    source_path = root_path / "lectures" / "tlb.md"; source_path.parent.mkdir()
    source_path.write_text("TLB", encoding="utf-8")
    now = datetime.now(UTC)
    sources = SourceRepository(database)
    source = sources.add(Source(
        None, source_path, "d" * 64, SourceType.MARKDOWN, 3, now, now, now,
    ))
    reader = StewardReadApplication(
        sources, SourceFragmentRepository(database),
        LexicalSearchService(sources, SourceFragmentRepository(database)), ActivityService(database), tmp_path / "inbox", roots=roots,
    )

    card = reader.source(str(source.id))

    assert isinstance(card, PresentedReply)
    assert "Location: CS3210 / lectures\\tlb.md" in card.text
    assert str(root_path) not in card.text


def test_codex_handoff_without_ids_offers_an_inbox_picker(tmp_path: Path) -> None:
    database = tmp_path / "steward.db"
    initialize_database(database)
    inbox = tmp_path / "vault" / "inbox"; inbox.mkdir(parents=True)
    path = inbox / "incoming.md"; path.write_text("private source text", encoding="utf-8")
    now = datetime.now(UTC)
    sources = SourceRepository(database)
    source = sources.add(Source(
        None, path, "c" * 64, SourceType.MARKDOWN, path.stat().st_size, now, now, now,
    ))
    app = StewardCodexHandoffApplication(
        CodexHandoffService(sources, SourceRootRepository(database), tmp_path / ".steward", inbox),
        sources,
        inbox,
    )

    picker = app.handle_command(make_event(text="/codex_handoff"))
    prepared = app.handle_command(make_event(text=f"/codex_handoff {source.id}"))

    assert isinstance(picker, PresentedReply)
    assert "incoming.md" in picker.text
    assert any(action.command == f"/codex_handoff {source.id}" for action in picker.actions)
    assert isinstance(prepared, PresentedReply)
    assert "metadata and guidance paths only" in prepared.text


def test_source_search_parses_authorized_root_and_type_filters(tmp_path: Path) -> None:
    database = tmp_path / "steward.db"
    initialize_database(database)
    root_path = tmp_path / "CS3210"; root_path.mkdir()
    roots = SourceRootRepository(database)
    roots.add("CS3210", root_path)
    calls: list[tuple[str, dict[str, object]]] = []

    class Lexical:
        def search(self, query: str, **kwargs: object) -> tuple[object, ...]:
            calls.append((query, kwargs))
            return ()

    reader = StewardReadApplication(
        SourceRepository(database), SourceFragmentRepository(database), Lexical(), # type: ignore[arg-type]
        ActivityService(database), tmp_path / "inbox", roots=roots,
    )

    response = reader.handle_command(make_event(text='/search TLB --type pdf --root "CS3210"'))

    assert response == "No local source fragments matched: 'TLB'."
    assert calls == [("TLB", {"limit": 5, "source_types": (SourceType.PDF,), "path_prefix": root_path})]


def test_source_search_falls_back_to_filename_metadata_when_no_fragment_matches(tmp_path: Path) -> None:
    database = tmp_path / "steward.db"; initialize_database(database)
    root_path = tmp_path / "CS4226"; root_path.mkdir()
    roots = SourceRootRepository(database); roots.add("CS4226", root_path)
    path = root_path / "lecture-network-architecture.pdf"; path.write_bytes(b"not parsed")
    now = datetime.now(UTC)
    sources = SourceRepository(database)
    source = sources.add(Source(None, path, "e" * 64, SourceType.PDF, path.stat().st_size, now, now, now))
    reader = StewardReadApplication(
        sources, SourceFragmentRepository(database),
        LexicalSearchService(sources, SourceFragmentRepository(database)), ActivityService(database), tmp_path / "inbox", roots=roots,
    )

    result = reader.handle_command(make_event(text="/search network architecture"))

    assert isinstance(result, PresentedReply)
    assert result.title == "Filename matches"
    assert "lecture-network-architecture.pdf" in result.text
    assert "CS4226 / lecture-network-architecture.pdf" in result.text
    assert result.actions[0].command == f"/source {source.id}"


def test_event_application_routes_owner_safe_reads(tmp_path: Path) -> None:
    database_path = tmp_path / "steward.db"
    initialize_database(database_path)
    inbox = tmp_path / "vault" / "inbox"
    inbox.mkdir(parents=True)
    source_path = inbox / "openmp.md"
    source_path.write_text("OpenMP notes", encoding="utf-8")
    now = datetime(2026, 9, 8, tzinfo=UTC)
    sources = SourceRepository(database_path)
    source = sources.add(
        Source(None, source_path, "a" * 64, SourceType.MARKDOWN, 11, now, now, now)
    )
    fragments = SourceFragmentRepository(database_path)
    fragments.replace_for_source(
        ExtractionResult(
            source.id or 0,
            (SourceFragment(None, source.id or 0, "OpenMP", 0, "OpenMP scheduling notes", "lines 1-1"),),
        )
    )
    activity = ActivityService(database_path)
    activity.record(ActivityType.SOURCE_CAPTURED, object_id=str(source.id), details="Inbox capture")
    reads = StewardReadApplication(
        sources, fragments, LexicalSearchService(sources, fragments), activity, inbox,
    )
    application = StewardEventApplication(
        StewardQuestionApplication(FakeGraph()),
        StewardCaptureApplication(type("Capture", (), {})()),
        read_application=reads,
    )

    inbox_card = application.handle(make_event(text="/inbox"))
    assert isinstance(inbox_card, PresentedReply)
    assert inbox_card.title == "Inbox" and "openmp.md" in inbox_card.text
    search = application.handle(make_event(text="/search OpenMP"))
    assert isinstance(search, PresentedReply)
    assert search.title == "Search results" and "openmp.md" in search.text
    assert search.actions[0].label == "Open 1"
    activity_listing = application.handle(make_event(text="/activity"))
    assert isinstance(activity_listing, PresentedReply)
    assert activity_listing.title == "Recent activity"
    source_details = application.handle(make_event(text="/source 1"))
    assert isinstance(source_details, PresentedReply)
    assert source_details.title == "openmp.md"
    assert str(tmp_path) not in source_details.text
    assert any(action.command == "/codex_handoff 1" for action in source_details.actions)
    natural_inbox = application.handle(make_event(text="what is in my inbox"))
    assert isinstance(natural_inbox, PresentedReply)
    assert "openmp.md" in natural_inbox.text
    natural_activity = application.handle(make_event(text="show my recent activity"))
    assert isinstance(natural_activity, PresentedReply)
    assert natural_activity.title == "Recent activity"
    natural_search = application.handle(make_event(text="find my notes on OpenMP"))
    assert isinstance(natural_search, PresentedReply)
    assert natural_search.title == "Search results"
    organize = application.handle(make_event(text="organize my inbox"))
    assert isinstance(organize, PresentedReply)
    assert organize.title == "Inbox"


def test_telegram_source_reference_reopens_the_last_explicitly_opened_source_after_restart(tmp_path: Path) -> None:
    database = tmp_path / "steward.db"
    initialize_database(database)
    now = datetime(2026, 9, 10, tzinfo=UTC)
    source_path = tmp_path / "parallel-computing.md"
    source_path.write_text("# OpenMP", encoding="utf-8")
    sources = SourceRepository(database)
    source = sources.add(Source(None, source_path, "a" * 64, SourceType.MARKDOWN, 8, now, now, now))
    fragments = SourceFragmentRepository(database)
    contexts = ReviewContextRepository(database)
    activity = ActivityService(database)

    def read_application() -> StewardReadApplication:
        return StewardReadApplication(
            sources, fragments, LexicalSearchService(sources, fragments),
            activity, tmp_path / "inbox", contexts=contexts,
        )

    class QuestionMustNotRun:
        def invoke(self, _input, _config=None):
            raise AssertionError("an exact source-reference request must not enter retrieval")

    first = StewardEventApplication(
        StewardQuestionApplication(QuestionMustNotRun()),
        StewardCaptureApplication(type("Capture", (), {})()),
        read_application=read_application(),
    )
    opened = first.handle(make_event(text=f"/source {source.id}"))

    assert isinstance(opened, PresentedReply)
    assert opened.title == "parallel-computing.md"
    assert opened.reference == ("source", source.id)
    context = contexts.get("telegram", "100")
    assert context is not None and context.kind == "source"

    restarted = StewardEventApplication(
        StewardQuestionApplication(QuestionMustNotRun()),
        StewardCaptureApplication(type("Capture", (), {})()),
        read_application=read_application(),
    )
    reopened = restarted.handle(make_event(text="open the last source"))

    assert isinstance(reopened, PresentedReply)
    assert reopened.title == "parallel-computing.md"
    empty_content = restarted.handle(make_event(text="give me the content"))
    assert isinstance(empty_content, PresentedReply)
    assert empty_content.reference == ("source", source.id)
    assert "does not establish that the document is empty" in empty_content.text
    assert "steward reextract" in empty_content.text
    assert empty_content.actions[0].command == f"/source {source.id}"
    assert fragments.list_for_source(source.id) == ()
    for suffix, expected_hint in (
        (".pdf", "Poppler"),
        (".png", "Tesseract"),
        (".docx", "Office Open XML"),
        (".html", "JavaScript"),
        (".eml", "attachments"),
        (".txt", "UTF-8"),
    ):
        sources.update(replace(source, path=source.path.with_suffix(suffix)))
        recovery = restarted.handle(make_event(text="give me the content"))
        assert expected_hint in recovery.text
    sources.update(source)

    fragments.replace_for_source(ExtractionResult(source.id, (
        SourceFragment(None, source.id, "OpenMP", 0, "Parallel loops", "page 1"),
        SourceFragment(None, source.id, "Scheduling", 1, "Static scheduling", "page 2"),
    )))
    content = restarted.handle(make_event(text="give me the content"))
    assert isinstance(content, PresentedReply)
    assert content.reference == ("source", source.id)
    assert "Parallel loops" in content.text and "page 1" in content.text
    assert "Static scheduling" not in content.text
    assert any(action.command == f"/summarize_source {source.id}" for action in content.actions)
    assert any(action.command == f"/ask_source {source.id}" for action in content.actions)
    next_action = next(action for action in content.actions if action.label == "Next")
    second = restarted.handle(make_event(text=next_action.command))
    assert "Static scheduling" in second.text and "page 2" in second.text
    assert "Parallel loops" not in second.text
    assert "between 1 and 2" in restarted.handle(make_event(text=f"/source_content {source.id} 3"))
    contexts.clear("telegram", "100")
    assert "Open a source" in restarted.handle(make_event(text="give me the content"))

    class SummaryModel:
        def __init__(self):
            self.inputs = []
            self.answer = "Parallel loops use scheduling. [F1]"

        def generate(self, *, instructions, input_text):
            self.inputs.append(input_text)
            return self.answer

    model = SummaryModel()
    privacy = PrivacyService(database)
    reader = StewardReadApplication(
        sources, fragments, LexicalSearchService(sources, fragments),
        activity, tmp_path / "inbox", contexts=contexts,
        source_model=model, source_model_allowed=privacy.permits_external_model,
    )
    reader.handle_command(make_event(text=f"/source {source.id}"))
    summary = reader.resolve_source_reference(make_event(text="summarize it"))
    assert summary.reference == ("source", source.id)
    assert "Generated summary of 2 extracted sections" in summary.text
    assert "Parallel loops" in model.inputs[0] and "Static scheduling" in model.inputs[0]
    assert str(tmp_path) not in model.inputs[0]
    direct_reply = reader.resolve_source_reference(replace(
        make_event(text="What scheduling is mentioned?", reply_text="Source card for parallel-computing.md"),
        reply_to_id="source-card",
    ))
    assert isinstance(direct_reply, PresentedReply)
    assert direct_reply.title == "Answer: parallel-computing.md"
    assert "Evidence locations:" in direct_reply.text
    assert "Question: What scheduling is mentioned?" in model.inputs[-1]
    prompt = reader.handle_command(make_event(text=f"/ask_source {source.id}"))
    assert prompt.title == "Ask about this source"
    restarted_reader = StewardReadApplication(
        sources, fragments, LexicalSearchService(sources, fragments),
        activity, tmp_path / "inbox", contexts=ReviewContextRepository(database),
        source_model=model, source_model_allowed=privacy.permits_external_model,
    )
    answer = restarted_reader.resolve_source_reference(make_event(text="What scheduling is mentioned?"))
    assert answer.title == "Answer: parallel-computing.md"
    assert "Question: What scheduling is mentioned?" in model.inputs[-1]
    assert contexts.get("telegram", "100").kind == "source"
    for invalid_answer in ("Unsupported statement [F99999]", "No citation at all"):
        model.answer = invalid_answer
        unverified = reader.resolve_source_reference(make_event(text="summarize it"))
        assert unverified.title == "Summary needs verification"
        assert invalid_answer not in unverified.text
    calls_before_denial = len(model.inputs)
    privacy.set_rule(source.id, PrivacyRule.NO_MODEL)
    denied = reader.resolve_source_reference(make_event(text="summarize it"))
    assert isinstance(denied, PresentedReply)
    assert "privacy rule" in denied.text
    assert denied.actions[1].command == f"/privacy_options {source.id}"
    assert len(model.inputs) == calls_before_denial


def test_telegram_privacy_change_is_reviewed_before_it_changes_model_access(tmp_path: Path) -> None:
    database_path = tmp_path / "steward.db"; initialize_database(database_path)
    now = datetime(2026, 9, 9, tzinfo=UTC)
    path = tmp_path / "note.md"; path.write_text("note", encoding="utf-8")
    sources = SourceRepository(database_path)
    source = sources.add(Source(None, path, "b" * 64, SourceType.MARKDOWN, 4, now, now, now))
    activity = ActivityService(database_path)
    proposals = ActionProposalRepository(database_path)
    privacy = PrivacyService(database_path)
    application = StewardEventApplication(
        StewardQuestionApplication(FakeGraph()), StewardCaptureApplication(type("Capture", (), {})()),
        privacy_application=StewardPrivacyApplication(privacy, sources, activity, proposals),
    )

    proposed = application.handle(make_event(text=f"/set_privacy {source.id} no_model"))

    assert isinstance(proposed, PresentedReply)
    assert proposed.title == "Review privacy change"
    assert proposed.actions[0].command == f"/source {source.id}"
    assert privacy.rule_for(source.id or 0) is PrivacyRule.EXTERNAL_ALLOWED
    assert proposals.get(1).status == "pending"

    conflicting = application.handle(make_event(text=f"/set_privacy {source.id} local_model_only"))
    assert isinstance(conflicting, PresentedReply)
    assert conflicting.title == "Privacy change pending"
    assert [action.command for action in conflicting.actions] == ["/approve_action 1", "/reject_action 1"]
    assert len(proposals.list_all()) == 1

    accepted = application.handle(make_event(text="/approve_action 1"))

    assert isinstance(accepted, PresentedReply)
    assert accepted.title == "Privacy rule applied"
    assert accepted.reference == ("source", source.id)
    assert [action.command for action in accepted.actions] == [
        f"/source {source.id}", f"/privacy_options {source.id}", "/home",
    ]
    assert privacy.rule_for(source.id or 0) is PrivacyRule.NO_MODEL
    assert proposals.get(1).status == "accepted"
    repeated = application.handle(make_event(text="/approve_action 1"))
    assert repeated == "Privacy proposal 1 was already accepted."


def test_paginated_source_cards_show_stable_source_ids(tmp_path: Path) -> None:
    database = tmp_path / "steward.db"
    initialize_database(database)
    sources = SourceRepository(database)
    fragments = SourceFragmentRepository(database)
    now = datetime(2026, 9, 16, tzinfo=UTC)
    for index in range(11):
        sources.add(Source(
            None, tmp_path / f"note-{index}.md", f"{index:064x}", SourceType.MARKDOWN,
            0, now, now, now,
        ))
    reader = StewardReadApplication(
        sources, fragments, LexicalSearchService(sources, fragments),
        ActivityService(database), tmp_path / "inbox",
    )

    first = reader.handle_command(make_event(text="/sources"))
    assert isinstance(first, PresentedReply)
    assert "ID 1" in first.text
    second = reader.handle_command(make_event(text="/sources 2"))
    assert isinstance(second, PresentedReply)
    assert "Page 2 of 2" in second.text
    assert "note-10.md" in second.text and "ID 11" in second.text
    assert any(action.command == "/source 11" for action in second.actions)

    detail = reader.handle_command(make_event(text="/source 11"))
    assert isinstance(detail, PresentedReply)
    assert "Source ID: 11" in detail.text
