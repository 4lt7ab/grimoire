# Glossary

Terms used across grimoire's code and docs, alphabetical.

---

**Database.** The SQLite file holding one grimoire — schema, entries, idx, FTS5 index, vec0 index, embedder lock. A mount can hold multiple databases.

**Embedder lock.** The `(model, dimension)` pair written into the file's `meta` table at create time. Validated against the supplied embedder on every reopen; mismatches raise `GrimoireMismatch`.

**Entry.** The identity row in `entry`: `(uniq_id, data)`. No filterable or searchable text lives on the entry — those are sidecars.

**`entry_idx`.** The filterable/sortable metadata sidecar. One row per indexed entry. Columns: `uniq_id` (PK), `uniq_ref`, `group_ref`, and five symmetric `ordinal_1`..`ordinal_5` columns (BLOB-affinity — any storage class accepted). Written by `index(uniq_id, ref=..., group=..., ord=...)`; cleaned by the entry-delete trigger.

**`entry_fts`.** The FTS5 keyword sidecar. One row per FTS-indexed entry holding `(uniq_id, text)`. Written by `index(uniq_id, match=...)`; cleaned by the entry-delete trigger. An entry without an `entry_fts` row is invisible to `match`.

**`entry_vec`.** The vec0 semantic sidecar. One row per embedded entry holding `(uniq_id, text, embedding)`. Written by `index(uniq_id, search=...)`; cleaned by the entry-delete trigger. An entry without an `entry_vec` row is invisible to `search`.

**`entry_delete_cascade`.** The `AFTER DELETE ON entry FOR EACH ROW` SQLite trigger that removes matching `uniq_id` rows from `entry_idx`, `entry_fts`, and `entry_vec`. Deleting an entry is the only public way to drop sidecar rows.

**Analyze.** `Grimoire.analyze()`. Runs SQLite's `ANALYZE` to refresh planner statistics. Run after bulk loads or distribution shifts so the planner can pick among the rotation composite indexes on `entry_idx` by selectivity.

**Fetch.** `Grimoire.fetch(uniq_refs)`. Looks up entries by `entry_idx.uniq_ref` (external reference). Returns parallel `(entries, indexes)` lists. `uniq_ref` is sparse-unique (UNIQUE index over the non-NULL rows), so each ref maps to at most one entry.

**FTS5.** SQLite's bundled full-text search extension. Powers `entry_fts` and `match`. Ranks by BM25.

**`group_ref`.** Non-unique TEXT column on `entry_idx` for grouping many entries under a shared key (e.g. a batch, shelf, tenant, or namespace id). Backed by a partial index (`WHERE group_ref IS NOT NULL`), so `query(Filters(equals={"group_ref": [...]}))` seeks rather than scans. Unlike `uniq_ref`, it enforces no uniqueness — any number of rows may share a `group_ref`. Written by `index(uniq_id, group=...)`.

**Index.** `Grimoire.index(uniq_id, *, ref, group, ord, match, search)`. The combined sidecar writer. PUT-style: each supplied kwarg wholesale-replaces the corresponding sidecar row; omitted kwargs leave that side alone. `ord` is a 5-tuple addressing `ordinal_1`..`ordinal_5`; in-tuple `None` writes NULL to that column.

**Match.** `Grimoire.match(query, filters=None, limit=10)`. FTS5 BM25 keyword search. Returns parallel `(entries, hits)` lists in rank order. `limit` defaults to 10; pass `None` to return every hit. `KeywordHit.score` is `-bm25` so higher = better.

**Mount.** Directory holding one default `grimoire.db`, optional named-subdirectory databases, a shared `__models__/` embedder cache, and a reserved `grimoire.toml` registry. The library publishes the convention via `grimoire.mount.Mount`; the CLI resolves a mount path from `--mount`, `$GRIMOIRE_MOUNT`, or `~/.grimoire`.

**`ordinal_1` .. `ordinal_5`.** Five consumer-defined columns on `entry_idx`. No declared type (BLOB-affinity): store any JSON-serializable scalar — categorical labels (`"draft"`, `"note"`), numeric measurements (timestamps, scores), or any other ordered value. Indexed; nullable. Filterable via `Filters.equals`, `Filters.gte`, `Filters.lte`. Comparison within a column follows SQLite's storage-class precedence; callers typically stick to one type per column.

**Peek.** A read-only inspection of a database that returns model, dimension, schema version, and per-table row counts without binding an embedder. Exposed as `Grimoire.peek(path)`.

**Query.** `Grimoire.query(filters=None, limit=100, cursor=None)`. Browses `entry_idx` rows ordered by `uniq_id` ASC, joined to `entry`. Returns parallel `(entries, indexes)` lists. Pages by `uniq_id` cursor; for ordinal-window paging, use `Filters.gte` / `Filters.lte`.

**Schema version.** Stored in the file's `PRAGMA user_version`, validated against the library's `SCHEMA_VERSION` on open. Pre-v1, mismatches raise `SchemaVersionError`; recreate the file.

**Search.** `Grimoire.search(query, limit=10)`. vec0 KNN semantic search. Returns parallel `(entries, hits)` lists nearest-first. `SemanticHit.distance` is the raw vec0 distance, non-negative.

**Sidecar.** A table keyed by `entry.uniq_id` that holds opt-in per-entry data (`entry_idx`, `entry_fts`, `entry_vec`). Sidecars don't have foreign keys to `entry` (virtual tables don't support FKs); the entry-delete trigger keeps them in sync.

**Telemetry.** Optional observability sink passed to `Grimoire.open(..., telemetry=...)`. A `Protocol` with two methods: `span(name, **attrs)` (context manager wrapping a block of work) and `event(name, **attrs)` (one-shot occurrence). Bundled: `NoOpTelemetry` (default — drops everything) and `LoggingTelemetry` (writes via stdlib `logging`, attaching structured fields under `extra={"grimoire": {...}}`). The CLI picks the sink via `$GRIMOIRE_TELEMETRY` (`off` | `logging`).

**ULID.** The id format grimoire assigns to every entry. Lexicographically sortable by creation time, which is how `query(cursor=...)` walks pages chronologically without a separate cursor column.

**`uniq_id`.** Primary key on `entry` and on all three sidecars. Library-assigned ULID at `add()` time.

**`uniq_ref`.** Sparse-unique TEXT column on `entry_idx` for external reference: a UNIQUE partial index covers the non-NULL rows, so any given non-NULL `uniq_ref` belongs to at most one entry. Looked up by `Grimoire.fetch(uniq_refs)`.
