# Glossary

Terms used across grimoire's code and docs, alphabetical.

---

**Database.** The SQLite file holding one grimoire — schema, entries, FTS5 index, vec0 index, embedder lock. A mount can hold multiple databases.

**Embedder lock.** The `(model, dimension)` pair written into the file's `meta` table at create time. Validated against the supplied embedder on every reopen; mismatches raise `GrimoireMismatch`.

**Entry.** The metadata row: `(id, group_key, group_ref, payload, context)`. No searchable text lives on the entry — that's what the keyword and semantic indexes are for.

**FTS5.** SQLite's bundled full-text search extension. Powers `entry_fts` and `keyword_search`. Ranks by BM25.

**`group_key` / `group_ref`.** Consumer-set labels on an entry. `(group_key, group_ref)` is enforced unique when both are set; nulls coexist freely. The same `group_ref` is allowed across different `group_key`s.

**Keyword index.** The `entry_fts` row holding the keyword text and `threshold_rank` for an entry. Written by `keyword()`, removed by `keyword_remove()`. An entry without a keyword index row is invisible to `keyword_search`.

**Mount.** Directory holding one default `grimoire.db`, optional named-subdirectory databases, a shared `__models__/` embedder cache, and a reserved `grimoire.toml` registry. The library publishes the convention via `grimoire.mount.Mount`; the CLI resolves a mount path from `--mount`, `$GRIMOIRE_MOUNT`, or `~/.grimoire`.

**Partition.** A vec0 partition key on the semantic index row. Lets `semantic_search(partition=...)` narrow KNN to a slice without scanning the rest. Distinct from `group_key` — `group_key` lives on the entry, `partition` lives on the vec row. The same id can move partitions by re-embedding.

**Peek.** A read-only inspection of a database that returns model, dimension, schema version, and counts without loading sqlite-vec or requiring an embedder. Exposed as `grimoire.peek(path)`.

**Schema version.** Stored in the file's `PRAGMA user_version`, validated against the library's `SCHEMA_VERSION` on open. Pre-v1, mismatches raise `SchemaVersionError`; recreate the file.

**Semantic index.** The `entry_vec` row holding the embedding, source text, partition, and `threshold_distance` for an entry. Written by `embed()`, removed by `embed_remove()`. An entry without a semantic index row is invisible to `semantic_search`.

**`threshold_rank` / `threshold_distance`.** Per-row similarity gates stored alongside an index row. Surfaced on hits (`KeywordHit.threshold_rank`, `SemanticHit.threshold_distance`); the library does not auto-filter by them.

**ULID.** The id format grimoire assigns to every entry. Lexicographically sortable by creation time, which is how `fetch(cursor=...)` walks pages chronologically without a separate cursor column.
