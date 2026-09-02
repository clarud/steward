# Steward

Steward is a local-first personal memory, knowledge, and action assistant. It preserves original sources, builds evidence-backed knowledge, and only takes consequential actions through controlled services.

## Current status

Phase 0 establishes the project foundation only. Steward does not yet read a vault, create a database, call a model, or connect to Telegram.

## Local setup

Requires Python 3.12 or newer.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
pytest
steward
```

Copy `.env.example` to `.env` only when you need local configuration. Never commit `.env`.

## Documentation

- `docs/product.md` — product intent
- `docs/architecture.md` — architectural boundaries
- `docs/invariants.md` — rules every feature must preserve
- `docs/adr/` — records of foundational decisions

