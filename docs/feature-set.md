# Feature set

**TL;DR:** A single-file SQLite + sqlite-vec datastore with semantic search, kind-partitioned records, per-record similarity gates, and a thin CLI.

**When to read this:** When deciding whether a proposed change is in scope.

---

## What this does

- **Single-file datastore.** One SQLite file is the entire grimoire — schema, entries, vectors. `Grimoire.init(path, embedder=...)` is the one-time setup that creates and prepares the file; `Grimoire.open(path, embedder=...)` opens an existing one and raises `GrimoireNotFound` on a missing or non-grimoire path.
- **Explicit init.** `Grimoire.init` (and `grimoire init` on the CLI) bundles file creation, lock-row write/validation, and a single embedder exercise (`embed(" ")`) into one idempotent step. Any deferred embedder work — model download, weight load — happens at a moment the caller chose, not silently inside the first `add` or `search`.
- **Semantic search.** `search(query, k=...)` returns entries ranked by vector distance against the embedded query.
- **Kind partitioning.** Every entry has a `kind` label. Reads (`search`, `list`) accept an optional `kind=` filter; the vector index is partitioned on `kind` so filtered search stays cheap.
- **Per-record similarity thresholds.** Records can carry a `threshold`. `search(..., dynamic_threshold=True)` drops results that don't clear each record's own gate — useful for heuristic-driven filtering where different records demand different match tightness.
- **ULID identifiers.** Entries are addressed by ULID, so chronological order is implicit in the id. `list` paginates via `after_id`, and `Entry.created_at` exposes the ULID's timestamp without an extra column.
- **Age-windowed reads.** Both `list` and `search` accept `created_after` / `created_before` (inclusive lower, exclusive upper) to restrict results to a time window. The window is enforced as an index range on the ULID id — no `created_at` column, no new index.
- **Optional payloads.** Each entry carries an optional JSON-object payload alongside its content for caller-specific metadata. Passed in and returned as a `dict` — callers can pipe it straight into a Pydantic model or dataclass without re-parsing.
- **Embedder Protocol.** Callers supply any object satisfying the `Embedder` Protocol (`model`, `dimension`, `embed`). A `FastembedEmbedder` is bundled behind the `fastembed` extra.
- **Embedder lock.** The embedding model name and dimension are written into the file on first open. Reopening with a mismatched embedder raises `GrimoireMismatch` rather than silently producing nonsense vectors.
- **File inspection without opening.** `Grimoire.peek(path)` returns model, dimension, schema version, total entry count, and per-kind counts without loading sqlite-vec or requiring an embedder.
- **CLI.** `grimoire {init, info, add, ingest, search, list, get, delete}` operates on a grimoire mount directory (`--mount <dir>` / `GRIMOIRE_MOUNT`), which holds the SQLite file and the embedder model cache. JSONL output everywhere makes it pipeable to `jq`. `grimoire --help` is the consolidated orientation: commands, the mount model, output conventions, and environment variables in one screen, intended to ground a new operator (human or LLM) without reaching for the README.

## What this does not do

- Serve as a general-purpose database. grimoire is a search-indexed datastore, not a relational store callers should reach into for arbitrary persistence.
- Manage embedders. Callers own their embedder lifecycle; grimoire only validates that the supplied embedder matches what the file was created with (and exercises it once during `init` to materialize deferred setup work).
- Multi-process write coordination. SQLite's own locking is the only synchronization — fine for single-writer use, not designed for high-concurrency producers.
