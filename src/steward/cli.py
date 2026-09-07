"""Command-line entry point for Steward."""

from __future__ import annotations

import argparse
import logging
import os
import sqlite3
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

from steward.config import Settings, load_environment_file
from steward.application import StewardCaptureApplication, StewardEventApplication, StewardQuestionApplication
from steward.capture import InboxCaptureService
from steward.answer import (
    AnswerService,
    ContextBuilder,
    GeminiModelGateway,
    ModelGateway,
    OpenAIModelGateway,
)
from steward.extraction import MarkdownExtractor, SourceFragmentRepository
from steward.graphs import build_retrieval_answer_graph
from steward.graphs import GeminiToolCallingModel, build_tool_agent_graph
from steward.logging import configure_logging
from steward.sources import SourceRepository
from steward.sources.service import SourceService
from steward.storage import initialize_database
from steward.retrieval import (
    HybridRetriever,
    LexicalSearchService,
    SemanticSearchService,
    SentenceTransformerEmbeddingProvider,
    SQLiteSemanticIndex,
)
from steward.telegram import run_telegram_polling
from steward.workspaces import WorkspaceRepository, WorkspaceService
from steward.organization import OrganizationApprovalService, OrganizationProposalRepository, OrganizationService
from steward.activity import ActivityService, ActivityType
from steward.actions import FileMutationService
from steward.records import RecordService
from steward.calendar import CalendarService, CalendarWriteService, GOOGLE_CALENDAR_EVENTS_SCOPE, authorize_google_calendar
from steward.knowledge import KnowledgeService
from steward.tools import CalendarReadToolService, ReadOnlyToolService, ToolPolicy, build_calendar_read_tools, build_read_only_tools
from steward.tools.read_only import READ_ONLY_TOOL_DEFINITIONS
from steward.tools.calendar_read import CALENDAR_READ_TOOL_DEFINITIONS
from langgraph.checkpoint.sqlite import SqliteSaver
from langchain_core.messages import HumanMessage, SystemMessage


def build_parser() -> argparse.ArgumentParser:
    """Create the command-line interface for currently available features."""
    parser = argparse.ArgumentParser(prog="steward")
    subcommands = parser.add_subparsers(dest="command")
    scan_parser = subcommands.add_parser("scan", help="Register Markdown files under a root")
    scan_parser.add_argument("root", type=Path, help="Directory containing Markdown files")
    index_parser = subcommands.add_parser(
        "index", help="Scan Markdown files and build their local semantic index"
    )
    index_parser.add_argument("root", type=Path, help="Directory containing Markdown files")
    subcommands.add_parser(
        "download-embedding-model",
        help="Download Steward's local embedding model for semantic search",
    )
    subcommands.add_parser("sources", help="List registered sources")
    search_parser = subcommands.add_parser("search", help="Search indexed Markdown fragments")
    search_parser.add_argument("query", help="Terms to search for")
    search_parser.add_argument("--limit", type=int, default=5, help="Maximum matches")
    semantic_parser = subcommands.add_parser(
        "semantic-search", help="Search Markdown fragments by meaning"
    )
    semantic_parser.add_argument("query", help="A natural-language question or phrase")
    semantic_parser.add_argument("--limit", type=int, default=5, help="Maximum matches")
    hybrid_parser = subcommands.add_parser(
        "hybrid-search", help="Combine lexical and semantic Markdown search"
    )
    hybrid_parser.add_argument("query", help="Terms or a natural-language question")
    hybrid_parser.add_argument("--limit", type=int, default=5, help="Maximum matches")
    ask_parser = subcommands.add_parser(
        "ask", help="Answer a question from retrieved local source fragments"
    )
    ask_parser.add_argument("question", help="Question to answer from local evidence")
    ask_parser.add_argument("--limit", type=int, default=5, help="Maximum evidence fragments")
    agent_parser = subcommands.add_parser(
        "agent", help="Answer using Steward's read-only tool-calling loop"
    )
    agent_parser.add_argument("question", help="Question the agent may answer with local read-only tools")
    agent_parser.add_argument("--thread-id", default="cli:agent", help="Persistent LangGraph thread ID")
    agent_parser.add_argument("--include-calendar", action="store_true", help="Allow current Google Calendar read tools after OAuth")
    telegram_parser = subcommands.add_parser(
        "telegram", help="Run the local Telegram adapter with long polling"
    )
    telegram_parser.add_argument(
        "--limit", type=int, default=5, help="Maximum evidence fragments per question"
    )
    workspace_parser = subcommands.add_parser("create-workspace", help="Create an explicit workspace")
    workspace_parser.add_argument("name")
    subcommands.add_parser("workspaces", help="List workspaces")
    link_parser = subcommands.add_parser("link-source", help="Relate a source to a workspace")
    link_parser.add_argument("workspace_id", type=int)
    link_parser.add_argument("source_id", type=int)
    propose_parser = subcommands.add_parser("propose-organization", help="Create a non-mutating organization proposal")
    propose_parser.add_argument("source_id", type=int)
    subcommands.add_parser("organization-proposals", help="List organization proposals")
    subcommands.add_parser("activity", help="List recent activity events")
    review_parser = subcommands.add_parser("review-proposal", help="Accept or reject an organization proposal")
    review_parser.add_argument("proposal_id", type=int)
    review_parser.add_argument("status", choices=("accepted", "rejected"))
    travel_parser = subcommands.add_parser("propose-travel-record", help="Interpret source fragments as a travel record")
    travel_parser.add_argument("source_id", type=int)
    create_travel_parser = subcommands.add_parser(
        "create-travel-record", help="Persist an evidence-backed travel record proposed from a source"
    )
    create_travel_parser.add_argument("source_id", type=int)
    subcommands.add_parser("travel-records", help="List saved travel records")
    calendar_authorize = subcommands.add_parser(
        "calendar-authorize", help="Authorize local read-only Google Calendar access"
    )
    calendar_authorize.add_argument("client_secrets", type=Path, help="Google OAuth desktop-client JSON file")
    calendar_authorize.add_argument("--token-file", type=Path)
    calendar_search = subcommands.add_parser("calendar-search", help="Search current Google Calendar events")
    calendar_search.add_argument("query", nargs="?", default="")
    calendar_search.add_argument("--after", type=datetime.fromisoformat)
    calendar_search.add_argument("--before", type=datetime.fromisoformat)
    calendar_search.add_argument("--limit", type=int, default=10)
    calendar_search.add_argument("--client-secrets", type=Path)
    calendar_get = subcommands.add_parser("calendar-get", help="Read one current Google Calendar event")
    calendar_get.add_argument("event_id")
    calendar_get.add_argument("--client-secrets", type=Path)
    calendar_create = subcommands.add_parser(
        "calendar-create-travel-event", help="Create an approved Google Calendar event from a travel record"
    )
    calendar_create.add_argument("record_id", type=int)
    calendar_create.add_argument("--client-secrets", type=Path)
    return parser


def _model_gateway_from_settings(
    settings: Settings, *, command: str
) -> ModelGateway | None:
    """Create the configured model gateway, or print its actionable setup error."""

    if settings.model_provider == "gemini":
        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key or not settings.gemini_model:
            print(
                "Set GEMINI_API_KEY and STEWARD_GEMINI_MODEL before using "
                f"`steward {command}`."
            )
            return None
        return GeminiModelGateway(api_key=api_key, model=settings.gemini_model)

    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key or not settings.openai_model:
        print(
            "Set OPENAI_API_KEY and STEWARD_OPENAI_MODEL before using "
            f"`steward {command}`."
        )
        return None
    return OpenAIModelGateway(api_key=api_key, model=settings.openai_model)


def _build_question_graph(
    settings: Settings, model_gateway: ModelGateway, *, limit: int
):
    """Compose the reusable local retrieval-and-answer workflow."""

    database_path = settings.data_dir / "steward.db"
    initialize_database(database_path)
    source_repository = SourceRepository(database_path)
    fragment_repository = SourceFragmentRepository(database_path)
    semantic_search = SemanticSearchService(
        source_repository,
        SQLiteSemanticIndex(database_path, SentenceTransformerEmbeddingProvider()),
    )
    retriever = HybridRetriever(
        LexicalSearchService(source_repository, fragment_repository), semantic_search
    )
    answer_service = AnswerService(
        retriever=retriever,
        context_builder=ContextBuilder(),
        model_gateway=model_gateway,
    )
    checkpoint_connection = sqlite3.connect(
        settings.data_dir / "checkpoints.db", check_same_thread=False
    )
    checkpointer = SqliteSaver(checkpoint_connection)
    checkpointer.setup()
    return build_retrieval_answer_graph(
        retriever, answer_service, retrieval_limit=limit, checkpointer=checkpointer
    )


def main(argv: Sequence[str] | None = None) -> None:
    """Run a Steward command."""
    load_environment_file()
    arguments = build_parser().parse_args(argv)
    settings = Settings.from_environment()
    configure_logging(settings)

    if arguments.command == "scan":
        database_path = settings.data_dir / "steward.db"
        initialize_database(database_path)
        result = SourceService(
            source_repository=SourceRepository(database_path),
            fragment_repository=SourceFragmentRepository(database_path),
            markdown_extractor=MarkdownExtractor(),
        ).scan_markdown_root(arguments.root)
        print(
            "Scan complete: "
            f"new={result.new} updated={result.updated} "
            f"unchanged={result.unchanged} missing={result.missing}"
        )
        return

    if arguments.command == "index":
        database_path = settings.data_dir / "steward.db"
        initialize_database(database_path)
        provider = SentenceTransformerEmbeddingProvider()
        result = SourceService(
            source_repository=SourceRepository(database_path),
            fragment_repository=SourceFragmentRepository(database_path),
            markdown_extractor=MarkdownExtractor(),
            semantic_index=SQLiteSemanticIndex(database_path, provider),
        ).scan_markdown_root(arguments.root)
        print(
            "Index complete: "
            f"new={result.new} updated={result.updated} "
            f"unchanged={result.unchanged} missing={result.missing}"
        )
        return

    if arguments.command == "download-embedding-model":
        SentenceTransformerEmbeddingProvider(allow_download=True)
        print("Embedding model downloaded and ready for offline use.")
        return

    if arguments.command == "sources":
        database_path = settings.data_dir / "steward.db"
        initialize_database(database_path)
        sources = SourceRepository(database_path).list_all()
        if not sources:
            print("No sources registered.")
            return

        for source in sources:
            print(f"{source.id}\t{source.status.value}\t{source.path}")
        return

    if arguments.command == "search":
        database_path = settings.data_dir / "steward.db"
        initialize_database(database_path)
        hits = LexicalSearchService(
            source_repository=SourceRepository(database_path),
            fragment_repository=SourceFragmentRepository(database_path),
        ).search(arguments.query, limit=arguments.limit)
        if not hits:
            print("No matching fragments.")
            return

        for hit in hits:
            heading = hit.fragment.heading or "Preamble"
            snippet = " ".join(hit.fragment.text.split())
            print(f"{hit.source.path}:{hit.fragment.location} [{heading}]")
            print(f"  {snippet[:160]}")
        return

    if arguments.command in {"semantic-search", "hybrid-search"}:
        database_path = settings.data_dir / "steward.db"
        initialize_database(database_path)
        source_repository = SourceRepository(database_path)
        fragment_repository = SourceFragmentRepository(database_path)
        semantic_search = SemanticSearchService(
            source_repository,
            SQLiteSemanticIndex(database_path, SentenceTransformerEmbeddingProvider()),
        )
        if arguments.command == "semantic-search":
            hits = semantic_search.search(arguments.query, limit=arguments.limit)
        else:
            hits = HybridRetriever(
                LexicalSearchService(source_repository, fragment_repository), semantic_search
            ).search(arguments.query, limit=arguments.limit)
        if not hits:
            print("No matching fragments. Run `steward index <vault>` first.")
            return

        for hit in hits:
            heading = hit.fragment.heading or "Preamble"
            snippet = " ".join(hit.fragment.text.split())
            print(f"{hit.source.path}:{hit.fragment.location} [{heading}]")
            print(f"  {snippet[:160]}")
        return

    if arguments.command == "ask":
        model_gateway = _model_gateway_from_settings(settings, command="ask")
        if model_gateway is None:
            return
        graph_result = _build_question_graph(
            settings, model_gateway, limit=arguments.limit
        ).invoke({"question": arguments.question})
        print(graph_result["answer"])
        citations = graph_result.get("citations", ())
        if citations:
            print("\nSources:")
            for citation in citations:
                heading = citation.heading or "Preamble"
                print(
                    f"[{citation.key}] {citation.source_path}:"
                    f"{citation.location} [{heading}]"
                )
        return

    if arguments.command == "agent":
        if settings.model_provider != "gemini":
            print("`steward agent` currently supports the configured Gemini provider only.")
            return
        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key or not settings.gemini_model:
            print("Set GEMINI_API_KEY and STEWARD_GEMINI_MODEL before using `steward agent`.")
            return
        database_path = settings.data_dir / "steward.db"
        initialize_database(database_path)
        sources = SourceRepository(database_path)
        fragments = SourceFragmentRepository(database_path)
        tool_service = ReadOnlyToolService(
            sources,
            fragments,
            LexicalSearchService(sources, fragments),
            KnowledgeService(database_path),
            RecordService(database_path),
            WorkspaceRepository(database_path),
            ActivityService(database_path),
        )
        checkpoint_connection = sqlite3.connect(
            settings.data_dir / "checkpoints.db", check_same_thread=False
        )
        checkpointer = SqliteSaver(checkpoint_connection)
        checkpointer.setup()
        tools = build_read_only_tools(tool_service)
        definitions = list(READ_ONLY_TOOL_DEFINITIONS)
        if arguments.include_calendar:
            client_secrets = os.environ.get("STEWARD_GOOGLE_CLIENT_SECRETS")
            if not client_secrets:
                print("Set STEWARD_GOOGLE_CLIENT_SECRETS before using --include-calendar.")
                return
            calendar = CalendarService(
                authorize_google_calendar(Path(client_secrets), settings.data_dir / "config" / "google-calendar-token.json")
            )
            tools.extend(build_calendar_read_tools(CalendarReadToolService(calendar)))
            definitions.extend(CALENDAR_READ_TOOL_DEFINITIONS)
        graph = build_tool_agent_graph(
            GeminiToolCallingModel(api_key=api_key, model=settings.gemini_model),
            tools,
            checkpointer=checkpointer,
            tool_policy=ToolPolicy(definitions),
        )
        result = graph.invoke(
            {
                "messages": [
                    SystemMessage(
                        "You are Steward. Use only the supplied read-only tools when local information is needed. "
                        "Do not claim a result that a tool did not provide."
                    ),
                    HumanMessage(arguments.question),
                ]
            },
            {"configurable": {"thread_id": arguments.thread_id}, "recursion_limit": 8},
        )
        print(str(result["messages"][-1].content))
        return

    if arguments.command == "telegram":
        token = os.environ.get("TELEGRAM_BOT_TOKEN")
        if not token:
            print("Set TELEGRAM_BOT_TOKEN before using `steward telegram`.")
            return
        model_gateway = _model_gateway_from_settings(settings, command="telegram")
        if model_gateway is None:
            return
        graph = _build_question_graph(settings, model_gateway, limit=arguments.limit)
        capture_service = InboxCaptureService(
            settings.inbox_dir,
            SourceRepository(settings.data_dir / "steward.db"),
            SourceFragmentRepository(settings.data_dir / "steward.db"),
            ActivityService(settings.data_dir / "steward.db"),
        )
        application = StewardEventApplication(
            StewardQuestionApplication(graph), StewardCaptureApplication(capture_service)
        )
        run_telegram_polling(token, application, application)
        return

    if arguments.command == "calendar-authorize":
        token_path = arguments.token_file or settings.data_dir / "config" / "google-calendar-token.json"
        authorize_google_calendar(arguments.client_secrets, token_path)
        print(f"Google Calendar read access authorized. Token stored at {token_path}.")
        return

    if arguments.command in {"calendar-search", "calendar-get"}:
        client_secrets = arguments.client_secrets
        if client_secrets is None:
            configured = os.environ.get("STEWARD_GOOGLE_CLIENT_SECRETS")
            if not configured:
                print("Set STEWARD_GOOGLE_CLIENT_SECRETS or pass --client-secrets before reading Calendar.")
                return
            client_secrets = Path(configured)
        token_path = settings.data_dir / "config" / "google-calendar-token.json"
        calendar = CalendarService(authorize_google_calendar(client_secrets, token_path))
        if arguments.command == "calendar-search":
            events = calendar.search(arguments.query, time_min=arguments.after, time_max=arguments.before, limit=arguments.limit)
            for event in events:
                print(f"{event.id}\t{event.start}\t{event.end}\t{event.summary}")
        else:
            event = calendar.get_event(arguments.event_id)
            print(f"{event.id}\t{event.start}\t{event.end}\t{event.summary}")
        return

    if arguments.command == "calendar-create-travel-event":
        client_secrets = arguments.client_secrets
        if client_secrets is None:
            configured = os.environ.get("STEWARD_GOOGLE_CLIENT_SECRETS")
            if not configured:
                print("Set STEWARD_GOOGLE_CLIENT_SECRETS or pass --client-secrets before writing Calendar.")
                return
            client_secrets = Path(configured)
        database_path = settings.data_dir / "steward.db"
        initialize_database(database_path)
        record = next((item for item in RecordService(database_path).list_travel_records() if item.id == arguments.record_id), None)
        if record is None:
            print(f"Travel record {arguments.record_id} was not found.")
            return
        calendar = CalendarService(
            authorize_google_calendar(
                client_secrets,
                settings.data_dir / "config" / "google-calendar-token.json",
                scopes=(GOOGLE_CALENDAR_EVENTS_SCOPE,),
            )
        )
        event = CalendarWriteService(calendar, database_path, ActivityService(database_path)).create_travel_event(record)
        print(f"Calendar event {event.id} created or already linked.")
        return

    if arguments.command in {"create-workspace", "workspaces", "link-source"}:
        database_path = settings.data_dir / "steward.db"
        initialize_database(database_path)
        repository = WorkspaceRepository(database_path)
        service = WorkspaceService(repository, ActivityService(database_path))
        if arguments.command == "create-workspace":
            workspace = service.create(arguments.name)
            print(f"Created workspace {workspace.id}: {workspace.name}")
        elif arguments.command == "link-source":
            service.add_source(arguments.workspace_id, arguments.source_id)
            print(f"Linked source {arguments.source_id} to workspace {arguments.workspace_id}")
        else:
            for workspace in repository.list_all():
                print(f"{workspace.id}\t{workspace.status}\t{workspace.name}")
        return

    if arguments.command in {"propose-organization", "organization-proposals", "review-proposal"}:
        database_path = settings.data_dir / "steward.db"
        initialize_database(database_path)
        proposals = OrganizationProposalRepository(database_path)
        if arguments.command == "propose-organization":
            source = SourceRepository(database_path).get_by_id(arguments.source_id)
            if source is None:
                print(f"Source {arguments.source_id} was not found.")
                return
            proposal = OrganizationService().propose(source, WorkspaceRepository(database_path).list_all())
            proposal_id = proposals.add(proposal)
            ActivityService(database_path).record(
                ActivityType.ORGANIZATION_PROPOSED,
                object_id=str(proposal_id), details=proposal.rationale,
            )
            print(f"Created proposal {proposal_id}: {proposal.rationale}")
        elif arguments.command == "review-proposal":
            activity = ActivityService(database_path)
            approval = OrganizationApprovalService(
                proposals,
                SourceRepository(database_path),
                FileMutationService(SourceRepository(database_path), activity),
                activity,
            )
            approval.review(arguments.proposal_id, arguments.status)
            print(f"Proposal {arguments.proposal_id} {arguments.status}.")
        else:
            for proposal in proposals.list_all():
                print(
                    f"{proposal.id}\t{proposal.status}\t{proposal.proposal_type}\t"
                    f"confidence={proposal.confidence:.2f}\tsource={proposal.source_id}\t{proposal.rationale}"
                )
        return

    if arguments.command == "activity":
        database_path = settings.data_dir / "steward.db"
        initialize_database(database_path)
        for event in ActivityService(database_path).list_recent():
            print(f"{event.id}\t{event.event_type.value}\t{event.object_id or ''}\t{event.details}")
        return

    if arguments.command == "propose-travel-record":
        database_path = settings.data_dir / "steward.db"
        initialize_database(database_path)
        fragments = SourceFragmentRepository(database_path).list_for_source(arguments.source_id)
        proposal = RecordService(database_path).propose_travel_record(
            arguments.source_id, [(fragment.id or 0, fragment.text) for fragment in fragments]
        )
        record = proposal.record
        print(f"flight={record.flight_number or ''}\tdeparture={record.departure or ''}\tarrival={record.arrival or ''}\tevidence={proposal.field_evidence}")
        return

    if arguments.command == "create-travel-record":
        database_path = settings.data_dir / "steward.db"
        initialize_database(database_path)
        fragments = SourceFragmentRepository(database_path).list_for_source(arguments.source_id)
        records = RecordService(database_path)
        proposal = records.propose_travel_record(
            arguments.source_id, [(fragment.id or 0, fragment.text) for fragment in fragments]
        )
        record = records.create_from_proposal(proposal)
        print(f"Created travel record {record.id} from source {record.source_id}.")
        return

    if arguments.command == "travel-records":
        database_path = settings.data_dir / "steward.db"
        initialize_database(database_path)
        for record in RecordService(database_path).list_travel_records():
            print(f"{record.id}\t{record.flight_number or ''}\t{record.departure or ''}\t{record.arrival or ''}")
        return

    logging.getLogger(__name__).info("Steward foundation started")
    build_parser().print_help()


if __name__ == "__main__":
    main()
