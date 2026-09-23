# ADR-003: LangGraph orchestrates domain services

## Decision

Use LangGraph only to orchestrate existing services: the grounded
retrieval-answer workflow and the read-only `/agent` tool loop.

## Consequences

Domain logic remains ordinary Python and is testable without LangGraph. Future
multi-agent flows are built as LangGraph graphs and subgraphs over the same
services, not as a new framework.
