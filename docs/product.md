# Product

Steward is a single-user, local-first source memory and retrieval assistant.
Its active job is to make an existing personal file base easy to adopt, keep it
indexed as it changes, and retrieve the right source through CLI or Telegram
when a filename or location is vague.

The active loop is:

```text
authorize root → scan/extract → reconcile changes → search/retrieve → read or answer with provenance
```

Telegram also provides a local Inbox for deliberate captures and explicit Drive
or Gmail imports. Uploads do not bypass review, and a source's privacy policy
controls whether content may be sent to a configured model.

Original source files are canonical. SQLite stores paths, hashes, extraction
fragments, indexes, activity, and other operational or rebuildable data. A
source can be related to many ideas without being copied or moved.

`source_centric` is the default product mode. Workspace, Knowledge, Record,
Task, Calendar, research, and broad action subsystems are retained as legacy
implementation and are deliberately unwired from the default runtime. Their
presence in the repository is not an active product promise.
