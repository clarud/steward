# Core invariants

1. Original local sources remain authoritative; Steward never replaces them with AI output.
2. Only explicitly authorized roots and the configured Inbox are available to source services.
3. A source keeps stable identity through rescans, root relocation, and certain moves (exactly one missing and one new file share a content hash); hashes detect content changes.
4. Derived fragments, search indexes, embeddings, and summaries are rebuildable and keep source/fragment provenance.
5. A model receives only context that code retrieved for it. It has no filesystem, shell, or tool access, and every model role runs under a fixed call budget.
6. Deterministic code performs hashing, extraction, filesystem reads, database writes, move reconciliation, and every fallback decision.
7. Model output is data: a judge chooses only from supplied candidate IDs, and answers and summaries cite only supplied section keys. Anything else is rejected.
8. Answers and summaries cite supplied evidence. A cited statement whose citation is unknown or doesn't support it is removed, and the reply shows how many were removed.
9. Inbox capture is staged: nothing is saved until the owner chooses Save.
10. Steward never moves, renames, creates, or deletes user files; Codex does that. Scans are the reconciliation authority after any external change.
