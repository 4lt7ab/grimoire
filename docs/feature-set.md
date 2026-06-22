# Feature set

**TL;DR:** A single-file SQLite datastore. Entries are bare `(uniq_id, data)` rows; three opt-in sidecars attach typed filterable metadata (`entry_idx`), FTS5 keyword search (`entry_fts`), and vec0 semantic search (`entry_vec`). A combined `index()` writer fills any subset of sidecars in one PUT call; deletion cascades via DB trigger. A CLI with mount layout and an MCP server round it out.

**When to read this:** When deciding whether a proposed change is in scope.

---

## What this does

- **Single-file datastore.** One SQLite file is one grimoire — schema, entries, idx, FTS index, vec index. `Grimoire.open(path, embedder=...)` initializes an empty file or opens an existing one; mismatched embedders raise `GrimoireMismatch`.

- **Entry/sidecar separation.** Entries are pure identity: `uniq_id` + `data`. To make an entry searchable or filterable, call `index()` with the kwargs for the sides you want populated. An entry can have rows in zero, one, two, or all three sidecars — useful for data-only records, filter-only catalogs, keyword-only memory, vector-only embeddings, or any combination.

- **One-shot indexing.** `index(uniq_id, *, ref, group, ord, match, search)` PUT-replaces whichever sidecars its kwargs touch in a single call. Omit a kwarg to leave that side alone; pass it to overwrite end-to-end.

- **Filterable metadata.** `entry_idx` holds seven nullable columns: `uniq_ref` (TEXT — sparse-unique external reference), `group_ref` (TEXT — non-unique grouping key), and five symmetric `ordinal_1`..`ordinal_5` columns. The ordinals carry no declared type (BLOB-affinity) — store any JSON-serializable scalar; SQLite preserves the native storage class on the way in and out, and comparison follows class precedence. Each non-PK column is indexed. Library reads/writes them verbatim; semantics (what `ordinal_2` measures, what `ordinal_4` discriminates) are the caller's to define.

- **Grouping key.** `group_ref` is a non-unique TEXT column for grabbing many entries under one shared value — a batch, shelf, tenant, or namespace id. `query(Filters(equals={"group_ref": [...]}))` seeks its partial index rather than scanning. Distinct from `uniq_ref`, which stays globally unique; `group_ref` enforces no uniqueness, so any number of rows may share one.

- **Keyword search.** `match(query, filters=None, limit=10)` returns parallel `(entries, hits)` lists ranked by FTS5 BM25. `KeywordHit.score` is positive (higher = better). `limit` defaults to 10; pass `None` to return every hit. The CLI tokenizes free-form prose into safe quoted OR-joined FTS5 syntax for the common case.

- **Semantic search.** `search(query, limit=10)` embeds the query, runs vec0 KNN, and returns parallel `(entries, hits)` lists nearest-first by distance. `SemanticHit.distance` is the raw vec0 distance (lower = better).

- **Browse with filters.** `query(filters, limit, cursor)` walks `entry_idx` rows ordered by `uniq_id` ASC, joined to `entry` for the data side. Filters use a generic `equals`/`gte`/`lte` dict shape; column names are whitelisted against `entry_idx` columns.

- **Lookup by external reference.** `fetch(uniq_refs)` returns entries whose `entry_idx.uniq_ref` is in the given list. `uniq_ref` is sparse-unique (UNIQUE index over the non-NULL rows), so each ref maps to at most one entry.

- **Cascade-by-trigger.** An `AFTER DELETE ON entry` trigger cleans matching rows from `entry_idx`, `entry_fts`, and `entry_vec`. No `*_remove` helpers — deleting the entry is the only way to drop sidecar rows.

- **Paired-tuple reads.** `query`, `fetch`, `match`, `search` all return `(list[Entry], list[EntryIndex|Hit])` parallel arrays. `entries[i]` corresponds to `indexes[i]` / `hits[i]`. One SQL roundtrip per read — no follow-up `get()` needed.

- **ULID identifiers.** Entries are addressed by ULID, so chronological order is implicit in the id. `query(cursor=last_id)` walks pages in creation order without a separate cursor column.

- **JSON data column.** Every entry carries an optional `data` column. Any JSON-serializable value is accepted — object, array, scalar, or null. Round-tripped as-is.

- **Embedder protocol.** Callers supply any object implementing `model`, `dimension`, `embed(text)`, and `embed_many(texts)`. Two implementations ship with the library: `FastembedEmbedder` (ONNX-based, local, default `BAAI/bge-small-en-v1.5`) behind the `fastembed` extra, and `NoOpEmbedder` (zero vectors) for grimoires used only for keyword search or structured data.

- **Embedder lock.** The embedder's `model` and `dimension` are written into the file on first open. Reopening with a different `model` or `dimension` raises `GrimoireMismatch`.

- **Peek without opening.** `Grimoire.peek(path)` returns `model`, `dimension`, `schema_version`, and per-table row counts (`entry_count`, `entry_idx_count`, `entry_fts_count`, `entry_vec_count`). Does not require an embedder.

- **Planner-stats refresh.** `Grimoire.analyze()` (and `grimoire analyze` from the CLI) runs SQLite's `ANALYZE` to refresh `sqlite_stat1`. Run after bulk loads or distribution shifts so the planner can pick among the rotation composite indexes on `entry_idx` by selectivity.

- **Telemetry hook.** `Grimoire.open(..., telemetry=...)` accepts any object satisfying the `Telemetry` protocol (`span(name, **attrs)` context manager + `event(name, **attrs)` one-shot). Every public operation is wrapped in a span and key lifecycle moments emit events. Two implementations ship — `NoOpTelemetry` (default — drops everything) and `LoggingTelemetry` (writes via stdlib `logging` with structured fields). The CLI selects the sink via `$GRIMOIRE_TELEMETRY` (`off` | `logging`).

- **Mount layout (CLI).** The CLI organizes one or more grimoires under a mount directory: a default `grimoire.db`, optional named subdirectory databases, and a shared `__models__/` embedder cache. Mount resolution: `--mount` > `$GRIMOIRE_MOUNT` > `~/.grimoire`.

- **CLI.** `grimoire {mount, entry, info, analyze, query, fetch, match, search, mcp} ...` mirrors the library's surface plus mount administration. `entry add` and `entry update` accept idx + match + search flags that fold a PUT-index into the same call. Every command prints pretty-indented JSON.

- **MCP server.** `grimoire mcp serve` exposes the library's read+write surface over stdio as FastMCP tools, scoped to the mount picked at boot. Tools: `info`, `add`, `update`, `get`, `remove`, `query`, `fetch`, `match`, `search`. Both `add` and `update` accept the data + idx + match + search kwargs in a single call; there is no standalone `index` tool. Mount administration stays CLI-only.

## What this does not do

- **General-purpose database.** Grimoire is a search-indexed datastore, not a relational store callers reach into for arbitrary persistence.
- **Manage embedders.** Callers own their embedder's lifecycle. The library only validates that the supplied embedder matches what the file was created with.
- **Partial sidecar updates.** `index()` is PUT — passing `ref="X"` alone wipes any existing `ordinal_*` columns on `entry_idx`. There is no PATCH path; callers who want partial updates either pass every column they want kept, or fall back to driving the SQL themselves.
- **Per-sidecar removal.** Removing an entry cascade-cleans every sidecar via DB trigger. There is no public way to drop just one sidecar row while keeping the entry.
- **Uniqueness beyond `uniq_ref`.** Only `uniq_ref` enforces uniqueness (sparse: over the non-NULL rows). `group_ref` and the five `ordinal_*` columns are indexed but not unique — callers manage dedup on those columns themselves if they need it.
- **In-place schema migration.** Pre-v1, schema changes are not migrated. A `SCHEMA_VERSION` mismatch raises `SchemaVersionError`; the response is to recreate the file. Migration ergonomics get designed once v1 is on the table.
- **Multi-process write coordination.** SQLite's connection-level locking serializes writes. Suitable for one writer with many readers, not for sustained high-concurrency writes.
