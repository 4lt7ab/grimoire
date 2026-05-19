# Architecture

**TL;DR:** A core Python library wraps SQLite, sqlite-vec, and FTS5. Entries are bare identity rows; three opt-in sidecars (typed metadata, FTS keyword, vec semantic) attach to each entry's `uniq_id`. A CLI and an MCP server sit on top.

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

One identity table, three sidecars, one meta table:

- **`entry`** — `(uniq_id, data)`. The identity row. `uniq_id` is a ULID; `data` is a JSON-serialized value (object, array, scalar, or null), library-encoded on write and decoded on read.
- **`entry_idx`** — typed filterable/sortable metadata. Columns: `uniq_id` (PK), `uniq_ref` (TEXT), `nominal_1`, `nominal_2` (TEXT), `ordinal_1`, `ordinal_2`, `ordinal_3` (REAL). All columns except `uniq_id` are nullable; each non-PK column is indexed.
- **`entry_fts`** — virtual FTS5 table holding `(uniq_id, text)`. One row per FTS-indexed entry.
- **`entry_vec`** — virtual vec0 table holding `(uniq_id, text, embedding)`. One row per semantically-indexed entry.
- **`meta`** — key/value pairs recording the embedder lock (`model`, `dimension`) at create time.

The three sidecars are independent and opt-in. An entry can have rows in zero, one, two, or all three of them.

Indexing is decoupled from `add()`. The library's combined writer `Grimoire.index(uniq_id, *, ref, ord, nom, match, search)` PUT-replaces whichever sidecars its kwargs touch. The sidecars never reference the entry table via foreign key (virtual tables don't support FKs); instead, the trigger `entry_delete_cascade` fires `AFTER DELETE ON entry FOR EACH ROW` and removes any matching `uniq_id` from all three sidecars. There are no `*_remove` helpers — deleting the entry is the only way to drop sidecar rows.

## Lifecycle

- `Grimoire.open(path, *, embedder=None)` opens or initializes a SQLite file. Empty files get the schema installed and the embedder lock written. Existing files validate that the supplied embedder matches the locked `model` and `dimension` and raise `GrimoireMismatch` on disagreement. Without an embedder, an empty file locks to NoOp sentinel values; semantic operations later raise `EmbedderRequired`.
- `Grimoire` is a context manager. `__exit__` commits on a clean exit and rolls back on an unhandled exception.
- `Grimoire.peek(path)` reads `model`, `dimension`, `schema_version`, and per-table row counts without requiring an embedder. Safe to call against any file path.

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

Data enters and exits exclusively through the library. The CLI and MCP server are thin adapters: they translate command-line arguments or MCP tool calls into library calls, then serialize the returned dataclasses as JSON. Read commands that come back as paired `(entries, indexes|hits)` tuples are flattened to `[{"entry": ..., "<key>": ...}, ...]` for downstream `jq` / consumer use.
