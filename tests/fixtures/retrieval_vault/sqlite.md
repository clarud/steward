# SQLite

SQLite is an embedded database suitable for local operational metadata.

## Operational Metadata

Steward stores operational metadata such as source hashes, paths, and statuses in SQLite.

## FTS5

FTS5 builds an inverted index and uses BM25 for lexical ranking.

## Transactions

Transactions commit all related changes together or rollback partial changes after failure.
