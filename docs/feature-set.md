# Feature set

**TL;DR:** A single-file SQLite + sqlite-vec datastore with semantic search, kind-partitioned records, per-record similarity gates, and a thin CLI.

**When to read this:** When deciding whether a proposed change is in scope.

---

## What this does

- **Single-file datastore.** One SQLite file is the entire grimoire — schema, entries, vectors. `Grimoire.open(path)` is idempotent: it creates the file if missing and opens it if present.
- **Semantic search.** `search(query, k=...)` returns entries ranked by vector distance against the embedded query.
- **Kind partitioning.** Every entry has a `kind` label. Reads (`search`, `list`) accept an optional `kind=` filter; the vector index is partitioned on `kind` so filtered search stays cheap.
- **Per-record similarity thresholds.** Records can carry a `threshold`. `search(..., dynamic_threshold=True)` drops results that don't clear each record's own gate — useful for heuristic-driven filtering where different records demand different match tightness.
- **ULID identifiers.** Entries are addressed by ULID, so chronological order is implicit in the id (`list` paginates via `after_id`).
- **Optional payloads.** Each entry carries an opaque JSON payload alongside its content for caller-specific metadata.
- **Embedder Protocol.** Callers supply any object satisfying the `Embedder` Protocol (`model`, `dimension`, `embed`). A `FastembedEmbedder` is bundled behind the `fastembed` extra.
- **Embedder lock.** The embedding model name and dimension are written into the file on first open. Reopening with a mismatched embedder raises `GrimoireMismatch` rather than silently producing nonsense vectors.
- **File inspection without opening.** `Grimoire.peek(path)` returns model, dimension, schema version, total entry count, and per-kind counts without loading sqlite-vec or requiring an embedder.
- **CLI.** `grimoire {info, add, ingest, search, list, get, delete}` operates on a grimoire file. JSONL output everywhere makes it pipeable to `jq`.

## What this does not do

- Serve as a general-purpose database. grimoire is a search-indexed datastore, not a relational store callers should reach into for arbitrary persistence.
- Manage embedders. Callers own their embedder lifecycle; grimoire only validates that the supplied embedder matches what the file was created with.
- Multi-process write coordination. SQLite's own locking is the only synchronization — fine for single-writer use, not designed for high-concurrency producers.
