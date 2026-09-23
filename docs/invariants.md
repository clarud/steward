# Core invariants

1. Original local sources remain authoritative; Steward never silently replaces them with AI output.
2. Only explicitly authorized roots and the configured Inbox are available to source services.
3. A source keeps stable operational identity through ordinary rescans and approved root relocation; hashes detect content changes.
4. Derived fragments, search indexes, embeddings, and summaries remain rebuildable and retain source/fragment provenance where shown.
5. A model receives only context selected by retrieval and permitted by the source privacy rule; it has no arbitrary filesystem access.
6. Deterministic code performs hashing, extraction, filesystem reads, database writes, imports, and recovery decisions.
7. Inbox capture is safe under uncertainty: staging and explicit user review precede durable save or external import effects.
8. Retrieval answers distinguish source-backed evidence from generated explanation and preserve citations where available.
9. Steward never moves, renames, creates, or deletes user files; Codex performs file organisation with the owner's approval, and Steward reconciles the result.
10. Scans are the reconciliation authority after external tools edit, move, or rename files; watchers are convenience notifications, not proof of consistency.
