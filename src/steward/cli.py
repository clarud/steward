"""Command-line entry point for Steward."""

from __future__ import annotations

import argparse
import logging
import os
from collections.abc import Sequence
from pathlib import Path

from steward.config import Settings, load_environment_file
from steward.answer import (
    AnswerService,
    ContextBuilder,
    GeminiModelGateway,
    OpenAIModelGateway,
)
from steward.extraction import MarkdownExtractor, SourceFragmentRepository
from steward.graphs import build_retrieval_answer_graph
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
    return parser


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
        if settings.model_provider == "gemini":
            api_key = os.environ.get("GEMINI_API_KEY")
            if not api_key or not settings.gemini_model:
                print(
                    "Set GEMINI_API_KEY and STEWARD_GEMINI_MODEL before using "
                    "`steward ask`."
                )
                return
            model_gateway = GeminiModelGateway(
                api_key=api_key, model=settings.gemini_model
            )
        else:
            api_key = os.environ.get("OPENAI_API_KEY")
            if not api_key or not settings.openai_model:
                print(
                    "Set OPENAI_API_KEY and STEWARD_OPENAI_MODEL before using "
                    "`steward ask`."
                )
                return
            model_gateway = OpenAIModelGateway(
                api_key=api_key, model=settings.openai_model
            )
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
        graph_result = build_retrieval_answer_graph(
            retriever, answer_service, retrieval_limit=arguments.limit
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

    logging.getLogger(__name__).info("Steward foundation started")
    build_parser().print_help()


if __name__ == "__main__":
    main()
