# ADR-003: LangGraph orchestrates domain services

## Decision

Introduce LangGraph only for workflow orchestration after the underlying services exist.

## Consequences

Domain logic remains ordinary Python and is testable without LangGraph.

