# Feature set

**TL;DR:** A single-file SQLite datastore. Entries hold metadata; keyword (FTS5) and semantic (vec0 KNN) indexing are independent, opt-in operations against an entry id. Group labels, vec partitions, per-row similarity gates, a CLI with mount layout, and an MCP server round it out.

**When to read this:** When deciding whether a proposed change is in scope.

---

## What this does

- **Single-file datastore.** One SQLite file is one grimoire — schema, entries, FTS index, vector index. `grimoire.open(path, embedder=...)` initializes an empty file or opens an existing one; mismatched embedders raise `GrimoireMismatch`.

- **Entry/index separation.** Entries are pure metadata: `id`, `group_key`, `group_ref`, `payload`, `context`. To make an entry searchable, call `keyword(items)` to write an FTS5 row, `embed(items)` to write a vec0 row, or both. An entry can exist with neither, either, or both — useful for payload-only records, FTS-only catalogs, vector-only memory, or combined.

- **Re-indexing is cheap and explicit.** Calling `keyword()` or `embed()` on an id that already has an index row replaces it. The entry row is untouched. To "rename" the searchable text on a row, re-index — no need to delete and re-add.

- **Keyword search.** `keyword_search(query, filters, limit)` returns `KeywordHit`s ranked by FTS5 BM25. Each hit carries the matched `entry` (with its `keyword_text` and `threshold_rank` inline) and a positive `score` (higher = better). The CLI also tokenizes free-form prose into safe quoted OR-joined FTS5 syntax for the common case.

- **Semantic search.** `semantic_search(query, partition, limit)` embeds the query, runs vec0 KNN, and returns `SemanticHit`s. Each hit carries the matched `entry` (with its `semantic_text`, `partition`, and `threshold_distance` inline) and a `distance` (lower = better). Pass `partition` to narrow KNN to one partition; omit it to span every partition in the same query.

- **Partitions.** `entry_vec.partition` is a separate dimension from `entry.group_key`. Partitions are vec0 partition keys: KNN scoped to a partition skips other partitions at the index level. `group_key` is metadata on the entry, queryable from `fetch` and `keyword_search`. The two can move independently.

- **Per-row similarity gates.** Indexed rows can carry a `threshold_rank` (keyword) or `threshold_distance` (semantic). Stored on the index row, surfaced on hits. The library does not auto-filter by them — callers decide how to apply them in their own scoring logic.

- **Group metadata.** Optional `group_key` and `group_ref` on every entry. `(group_key, group_ref)` is enforced unique when both are set; nulls coexist freely. The same `group_ref` is allowed across different `group_key`s.

- **ULID identifiers.** Entries are addressed by ULID, so chronological order is implicit in the id. `fetch(cursor=last_id)` walks pages in creation order without a separate cursor column.

- **JSON payloads.** Every entry carries an optional JSON payload. Any JSON-serializable value is accepted — object, array, scalar, or null. Round-tripped as-is.

- **Filter sets.** `Filters(id=[...], group_key=[...], group_ref=[...])` lets `fetch` and `keyword_search` restrict by sets of values. All three filters are independently optional.

- **Wholesale updates.** `update(entries)` rewrites `group_key`, `group_ref`, `payload`, and `context` on existing rows. `id` is preserved; index rows are untouched. To make a partial update, fetch the entry, replace the fields you want to change, and pass it back. (The CLI offers a `--put` flag and defaults to fetch-then-patch.)

- **Embedder protocol.** Callers supply any object implementing `model`, `dimension`, `embed(text)`, and `embed_many(texts)`. Two implementations ship with the library: `FastembedEmbedder` (ONNX-based, local, default `BAAI/bge-small-en-v1.5`) behind the `fastembed` extra, and `NoOpEmbedder` (zero vectors) for grimoires that only use FTS or payload storage.

- **Embedder lock.** The embedder's `model` and `dimension` are written into the file on first open. Reopening with a different `model` or `dimension` raises `GrimoireMismatch`.

- **Peek without opening.** `grimoire.peek(path)` returns `model`, `dimension`, `schema_version`, entry count, per-group counts, and per-partition counts. Does not require an embedder.

- **Mount layout (CLI).** The CLI organizes one or more grimoires under a mount directory: a default `grimoire.db`, optional named subdirectory databases, and a shared `__models__/` embedder cache. Mount resolution: `--mount` > `$GRIMOIRE_MOUNT` > `~/.grimoire`.

- **CLI.** `grimoire {mount, entry, search, info, fetch, mcp} ...` mirrors the library's surface plus mount administration. `entry add` and `entry update` accept `--keyword-text` and `--semantic-text` flags that fold a (re-)index into the same call. Every command prints pretty-indented JSON.

- **MCP server.** `grimoire mcp serve` exposes the read+write surface over stdio as FastMCP tools, scoped to the mount picked at boot. Mount administration stays CLI-only; the MCP server operates on existing databases.

## What this does not do

- **General-purpose database.** Grimoire is a search-indexed datastore, not a relational store callers reach into for arbitrary persistence.
- **Manage embedders.** Callers own their embedder's lifecycle. The library only validates that the supplied embedder matches what the file was created with.
- **In-place schema migration.** Pre-v1, schema changes are not migrated. A `SCHEMA_VERSION` mismatch raises `SchemaVersionError`; the response is to recreate the file. Migration ergonomics get designed once v1 is on the table.
- **Multi-process write coordination.** SQLite's connection-level locking serializes writes. Suitable for one writer with many readers, not for sustained high-concurrency writes.
- **Auto-filter by stored thresholds.** `threshold_rank` and `threshold_distance` are stored on index rows and surfaced on hits. The library does not drop hits that fail them — that's the caller's policy.
