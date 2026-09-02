# Architecture

Steward grows as a normal local Python application. Domain services own domain logic and are independently testable. LangGraph, introduced only when needed, orchestrates those services rather than owning their logic.

The future transport path is Telegram adapter → event normalizer → application workflow → domain services. SQLite holds operational metadata; the filesystem remains the human-readable home for original sources.

