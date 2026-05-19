# Feature set

**TL;DR:** A single-file SQLite datastore. Entries are bare `(uniq_id, data)` rows; three opt-in sidecars attach typed filterable metadata (`entry_idx`), FTS5 keyword search (`entry_fts`), and vec0 semantic search (`entry_vec`). A combined `index()` writer fills any subset of sidecars in one PUT call; deletion cascades via DB trigger. A CLI with mount layout and an MCP server round it out.

**When to read this:** When deciding whether a proposed change is in scope.

---

## What this does

- **Single-file datastore.** One SQLite file is one grimoire — schema, entries, idx, FTS index, vec index. `Grimoire.open(path, embedder=...)` initializes an empty file or opens an existing one; mismatched embedders raise `GrimoireMismatch`.

- **Entry/sidecar separation.** Entries are pure identity: `uniq_id` + `data`. To make an entry searchable or filterable, call `index()` with the kwargs for the sides you want populated. An entry can have rows in zero, one, two, or all three sidecars — useful for data-only records, filter-only catalogs, keyword-only memory, vector-only embeddings, or any combination.

- **One-shot indexing.** `index(uniq_id, *, ref, ord, nom, match, search)` PUT-replaces whichever sidecars its kwargs touch in a single call. Omit a kwarg to leave that side alone; pass it to overwrite end-to-end.

- **Typed filterable metadata.** `entry_idx` holds seven nullable columns: `uniq_ref` (TEXT — external reference), `nominal_1`/`nominal_2` (TEXT — categorical labels), `ordinal_1`/`ordinal_2`/`ordinal_3` (REAL — sortable numbers). Each non-PK column is indexed. Library reads/writes them verbatim; semantics (what `nominal_1` means, what `ordinal_2` measures) are the caller's to define.

- **Keyword search.** `match(query, filters=None, limit=None)` returns parallel `(entries, hits)` lists ranked by FTS5 BM25. `KeywordHit.score` is positive (higher = better). The CLI tokenizes free-form prose into safe quoted OR-joined FTS5 syntax for the common case.

- **Semantic search.** `search(query, limit=10)` embeds the query, runs vec0 KNN, and returns parallel `(entries, hits)` lists nearest-first by distance. `SemanticHit.distance` is the raw vec0 distance (lower = better).

- **Browse with filters.** `query(filters, limit, cursor)` walks `entry_idx` rows ordered by `uniq_id` ASC, joined to `entry` for the data side. Filters use a generic `equals`/`gte`/`lte` dict shape; column names are whitelisted against `entry_idx` columns.

- **Lookup by external reference.** `fetch(uniq_refs)` returns entries whose `entry_idx.uniq_ref` is in the given list. Multiple entries may share a `uniq_ref` (no uniqueness constraint).

- **Cascade-by-trigger.** An `AFTER DELETE ON entry` trigger cleans matching rows from `entry_idx`, `entry_fts`, and `entry_vec`. No `*_remove` helpers — deleting the entry is the only way to drop sidecar rows.

- **Paired-tuple reads.** `query`, `fetch`, `match`, `search` all return `(list[Entry], list[EntryIndex|Hit])` parallel arrays. `entries[i]` corresponds to `indexes[i]` / `hits[i]`. One SQL roundtrip per read — no follow-up `get()` needed.

- **ULID identifiers.** Entries are addressed by ULID, so chronological order is implicit in the id. `query(cursor=last_id)` walks pages in creation order without a separate cursor column.

- **JSON data column.** Every entry carries an optional `data` column. Any JSON-serializable value is accepted — object, array, scalar, or null. Round-tripped as-is.

- **Embedder protocol.** Callers supply any object implementing `model`, `dimension`, `embed(text)`, and `embed_many(texts)`. Two implementations ship with the library: `FastembedEmbedder` (ONNX-based, local, default `BAAI/bge-small-en-v1.5`) behind the `fastembed` extra, and `NoOpEmbedder` (zero vectors) for grimoires used only for keyword search or structured data.

- **Embedder lock.** The embedder's `model` and `dimension` are written into the file on first open. Reopening with a different `model` or `dimension` raises `GrimoireMismatch`.

- **Peek without opening.** `Grimoire.peek(path)` returns `model`, `dimension`, `schema_version`, and per-table row counts (`entry_count`, `entry_idx_count`, `entry_fts_count`, `entry_vec_count`). Does not require an embedder.

- **Mount layout (CLI).** The CLI organizes one or more grimoires under a mount directory: a default `grimoire.db`, optional named subdirectory databases, and a shared `__models__/` embedder cache. Mount resolution: `--mount` > `$GRIMOIRE_MOUNT` > `~/.grimoire`.

- **CLI.** `grimoire {mount, entry, info, query, fetch, match, search, mcp} ...` mirrors the library's surface plus mount administration. `entry add` and `entry update` accept idx + match + search flags that fold a PUT-index into the same call. Every command prints pretty-indented JSON.

- **MCP server.** `grimoire mcp serve` exposes the library's read+write surface over stdio as FastMCP tools, scoped to the mount picked at boot. Tools: `info`, `add`, `update`, `get`, `remove`, `query`, `fetch`, `match`, `search`. Both `add` and `update` accept the data + idx + match + search kwargs in a single call; there is no standalone `index` tool. Mount administration stays CLI-only.

## What this does not do

- **General-purpose database.** Grimoire is a search-indexed datastore, not a relational store callers reach into for arbitrary persistence.
- **Manage embedders.** Callers own their embedder's lifecycle. The library only validates that the supplied embedder matches what the file was created with.
- **Partial sidecar updates.** `index()` is PUT — passing `ref="X"` alone wipes any existing `nominal_*` and `ordinal_*` columns on `entry_idx`. There is no PATCH path; callers who want partial updates either pass every column they want kept, or fall back to driving the SQL themselves.
- **Per-sidecar removal.** Removing an entry cascade-cleans every sidecar via DB trigger. There is no public way to drop just one sidecar row while keeping the entry.
- **Uniqueness constraints on sidecar columns.** `uniq_ref` is indexed but not unique; multiple entries may share one. Callers manage their own dedup if needed.
- **In-place schema migration.** Pre-v1, schema changes are not migrated. A `SCHEMA_VERSION` mismatch raises `SchemaVersionError`; the response is to recreate the file. Migration ergonomics get designed once v1 is on the table.
- **Multi-process write coordination.** SQLite's connection-level locking serializes writes. Suitable for one writer with many readers, not for sustained high-concurrency writes.
