# Architecture

Steward grows as a normal local Python application. Domain services own domain
logic and are independently testable. LangGraph, introduced only when needed,
orchestrates those services rather than owning their logic.

The first transport path is Telegram adapter → event normalizer → application
workflow → LangGraph retrieval/answer workflow. The Telegram adapter does not
perform retrieval or model calls itself: it translates a Telegram update into
an internal event, delegates it, and sends the returned text. SQLite holds
operational metadata; the filesystem remains the human-readable home for
original sources.
