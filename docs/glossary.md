# Glossary

Terms used across grimoire's code and docs, alphabetical.

---

**Database.** The SQLite file holding one grimoire — schema, entries, idx, FTS5 index, vec0 index, embedder lock. A mount can hold multiple databases.

**Embedder lock.** The `(model, dimension)` pair written into the file's `meta` table at create time. Validated against the supplied embedder on every reopen; mismatches raise `GrimoireMismatch`.

**Entry.** The identity row in `entry`: `(uniq_id, data)`. No filterable or searchable text lives on the entry — those are sidecars.

**`entry_idx`.** The typed filterable/sortable metadata sidecar. One row per indexed entry. Columns: `uniq_id` (PK), `uniq_ref`, `nominal_1`, `nominal_2`, `ordinal_1`, `ordinal_2`, `ordinal_3`. Written by `index(uniq_id, ref=..., nom=..., ord=...)`; cleaned by the entry-delete trigger.

**`entry_fts`.** The FTS5 keyword sidecar. One row per FTS-indexed entry holding `(uniq_id, text)`. Written by `index(uniq_id, match=...)`; cleaned by the entry-delete trigger. An entry without an `entry_fts` row is invisible to `match`.

**`entry_vec`.** The vec0 semantic sidecar. One row per embedded entry holding `(uniq_id, text, embedding)`. Written by `index(uniq_id, search=...)`; cleaned by the entry-delete trigger. An entry without an `entry_vec` row is invisible to `search`.

**`entry_delete_cascade`.** The `AFTER DELETE ON entry FOR EACH ROW` SQLite trigger that removes matching `uniq_id` rows from `entry_idx`, `entry_fts`, and `entry_vec`. Deleting an entry is the only public way to drop sidecar rows.

**Fetch.** `Grimoire.fetch(uniq_refs)`. Looks up entries by `entry_idx.uniq_ref` (external reference). Returns parallel `(entries, indexes)` lists. `uniq_ref` is non-unique, so the result may include more entries than refs.

**FTS5.** SQLite's bundled full-text search extension. Powers `entry_fts` and `match`. Ranks by BM25.

**Index.** `Grimoire.index(uniq_id, *, ref, ord, nom, match, search)`. The combined sidecar writer. PUT-style: each supplied kwarg wholesale-replaces the corresponding sidecar row; omitted kwargs leave that side alone.

**Match.** `Grimoire.match(query, filters=None, limit=None)`. FTS5 BM25 keyword search. Returns parallel `(entries, hits)` lists in rank order. `KeywordHit.score` is `-bm25` so higher = better.

**Mount.** Directory holding one default `grimoire.db`, optional named-subdirectory databases, a shared `__models__/` embedder cache, and a reserved `grimoire.toml` registry. The library publishes the convention via `grimoire.mount.Mount`; the CLI resolves a mount path from `--mount`, `$GRIMOIRE_MOUNT`, or `~/.grimoire`.

**`nominal_1` / `nominal_2`.** Two consumer-defined TEXT columns on `entry_idx`. Used for categorical labels (e.g., type, status, owner). Indexed; nullable. Filterable via `Filters.equals`.

**`ordinal_1` / `ordinal_2` / `ordinal_3`.** Three consumer-defined REAL columns on `entry_idx`. Used for sortable numerics (e.g., timestamps, scores, measurements). Indexed; nullable. Filterable via `Filters.equals`, `Filters.gte`, `Filters.lte`.

**Peek.** A read-only inspection of a database that returns model, dimension, schema version, and per-table row counts without binding an embedder. Exposed as `Grimoire.peek(path)`.

**Query.** `Grimoire.query(filters=None, limit=100, cursor=None)`. Browses `entry_idx` rows ordered by `uniq_id` ASC, joined to `entry`. Returns parallel `(entries, indexes)` lists. Pages by `uniq_id` cursor; for ordinal-window paging, use `Filters.gte` / `Filters.lte`.

**Schema version.** Stored in the file's `PRAGMA user_version`, validated against the library's `SCHEMA_VERSION` on open. Pre-v1, mismatches raise `SchemaVersionError`; recreate the file.

**Search.** `Grimoire.search(query, limit=10)`. vec0 KNN semantic search. Returns parallel `(entries, hits)` lists nearest-first. `SemanticHit.distance` is the raw vec0 distance, non-negative.

**Sidecar.** A table keyed by `entry.uniq_id` that holds opt-in per-entry data (`entry_idx`, `entry_fts`, `entry_vec`). Sidecars don't have foreign keys to `entry` (virtual tables don't support FKs); the entry-delete trigger keeps them in sync.

**ULID.** The id format grimoire assigns to every entry. Lexicographically sortable by creation time, which is how `query(cursor=...)` walks pages chronologically without a separate cursor column.

**`uniq_id`.** Primary key on `entry` and on all three sidecars. Library-assigned ULID at `add()` time.

**`uniq_ref`.** Indexed (but non-unique) TEXT column on `entry_idx` for external reference. Looked up by `Grimoire.fetch(uniq_refs)`.
