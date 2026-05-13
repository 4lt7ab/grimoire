# Architecture

**TL;DR:** A core Python library wraps SQLite, sqlite-vec, and FTS5. Entries hold metadata; keyword and semantic indexing are independent, opt-in operations against the same entry id. A CLI and an MCP server sit on top.

**When to read this:** Before making any change that crosses a component boundary.

---

## Components

- **`grimoire`** — the core library. Owns the SQLite schema, query access, and the domain types callers work with. All reads and writes pass through it.
- **`grimoire-cli`** — a management tool that wraps the library and adds a mount convention (named databases under one directory) plus an MCP server. Treated as the library's first user — its needs shape the public API.

Both packages live in the same uv workspace at `packages/`.

## How they connect

The CLI imports the library directly. There is no IPC, RPC, or daemon. The MCP server runs in-process over stdio. All three (library, CLI, MCP) share the underlying SQLite file on disk.

## External dependencies

- **SQLite** — the underlying datastore.
- **sqlite-vec** — vector index extension. Loaded as a runtime extension at connection open.
- **SQLite FTS5** — full-text index for keyword search. Bundled with SQLite, not a separate package.
- **Embedding model/provider** — produces the vectors that sqlite-vec stores. The bundled `FastembedEmbedder` is ONNX-based and runs locally; callers can supply any object satisfying the `Embedder` Protocol.
- **fastmcp** (CLI only) — powers the optional MCP server.

## Data model

Three tables back every grimoire:

- **`entry`** — `(id, group_key, group_ref, payload, context)`. The identity row. `id` is a ULID. `payload` is a JSON object. `context` is unindexed prose. No searchable text lives here.
- **`entry_fts`** — virtual FTS5 table holding `(entry_id, keyword_text, threshold_rank)`. One row per indexed entry.
- **`entry_vec`** — virtual vec0 table holding `(id, partition, semantic_text, threshold_distance, embedding)`. One row per embedded entry. `partition` is the vec0 partition key.

An entry can have a row in zero, one, or both of the index tables. Indexing is **not** a side-effect of `add()` — callers explicitly call `keyword()` or `embed()` against an existing entry id. This means an entry's searchable text can change after creation by re-indexing without touching the entry row.

A `meta` table records the embedder lock (`model`, `dimension`) at create time.

## Lifecycle

- `grimoire.open(path, *, embedder)` opens (or initializes) a SQLite file. Empty files get the schema installed and the embedder lock written. Existing files validate that the supplied embedder matches the locked `model` and `dimension` and raise `GrimoireMismatch` on disagreement.
- `Grimoire` is a context manager. `__exit__` commits on a clean exit and rolls back on an unhandled exception.
- `grimoire.peek(path)` reads metadata and counts (entries, per-group, per-partition) without requiring an embedder. Safe to call against any file path.

## Mount layout (CLI convention)

The CLI organizes one or more grimoires under a single directory:

```
<mount>/grimoire.db          # default DB
<mount>/<name>/grimoire.db   # a named DB
<mount>/__models__/          # shared embedder cache
<mount>/grimoire.toml        # registry file (reserved, currently inert)
```

The library publishes the `Mount` dataclass and the layout convention; the CLI decides where the mount lives on disk (`--mount` > `$GRIMOIRE_MOUNT` > `~/.grimoire`) and pairs it with the default `FastembedEmbedder`. Discovery of named DBs is by directory walk, not registry read — the manifest file exists but is reserved.

## Data flow

Data enters and exits exclusively through the library. The CLI and MCP server are thin adapters: they translate command-line arguments or MCP tool calls into library calls, then serialize the returned dataclasses as JSON.
