# Steward

Steward is a local-first personal memory, knowledge, and action assistant. It preserves original sources, builds evidence-backed knowledge, and only takes consequential actions through controlled services.

## Current status

Phase 4 is complete. Steward can register and structurally extract Markdown,
then retrieve its fragments with lexical, semantic, or hybrid search. It does
not yet answer questions with an LLM or connect to Telegram.

## Local setup

Requires Python 3.12 or newer.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
pytest
steward scan path\to\your\vault
```

Copy `.env.example` to `.env` only when you need local configuration. Never commit `.env`.

## Search a vault

Lexical search needs only the scan:

```powershell
steward scan path\to\your\vault
steward search "address translations"
```

Semantic and hybrid search use a local embedding model. Download it explicitly
once, then build the rebuildable local vector index. Subsequent indexing and
search run from the local model cache.

```powershell
steward download-embedding-model
steward index path\to\your\vault
steward semantic-search "the little cache CPUs use for address translation"
steward hybrid-search "the little cache CPUs use for address translation"
```

## Documentation

- `docs/developer-guide.md` — implementation, data flow, limitations, and next steps

- `docs/product.md` — product intent
- `docs/architecture.md` — architectural boundaries
- `docs/invariants.md` — rules every feature must preserve
- `docs/adr/` — records of foundational decisions
