# ADR-003: LangGraph orchestrates domain services

## Decision

Use LangGraph only to orchestrate existing services: the Find, Ask, and
Summarize flows ([ADR-009](ADR-009-minimal-surface-and-model-flows.md)).

## Consequences

Domain logic remains ordinary Python and is testable without LangGraph. Graph
nodes call services and model roles; they hold no SQL or filesystem logic.
Parallel steps use LangGraph fan-out edges and `Send`, not a separate
framework.
