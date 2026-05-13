# 4lt7ab-grimoire

The Python library behind grimoire — a single-file semantic datastore backed by SQLite and [`sqlite-vec`](https://github.com/asg017/sqlite-vec). Entries hold metadata; keyword (FTS5) and semantic (vec0) indexing are independent, opt-in operations against the same entry id.

For the standalone CLI, see [`4lt7ab-grimoire-cli`](https://pypi.org/project/4lt7ab-grimoire-cli/).

## Install

```sh
uv add '4lt7ab-grimoire[fastembed]'
```

The `fastembed` extra pulls the bundled `FastembedEmbedder` (ONNX-based, no service required). Drop the extra and implement the `Embedder` protocol to bring your own — see [Custom embedders](#custom-embedders).

## Mental model

A grimoire is a single SQLite file with three tables:

- `entry` — metadata: `(id, group_key, group_ref, payload, context)`. No searchable text lives here.
- `entry_fts` — FTS5 row holding `keyword_text` + `threshold_rank` for one entry.
- `entry_vec` — vec0 row holding `embedding` + `semantic_text` + `partition` + `threshold_distance` for one entry.

Plus a `meta` table that records the embedder's `model` and `dimension` at create time. Reopening with a mismatched embedder raises `GrimoireMismatch`.

**Indexing is decoupled from creation.** You `add()` an entry first, then `keyword()` and/or `embed()` it to make it searchable. An entry can have a row in zero, one, or both of the index tables. Re-indexing is explicit — call `keyword()` or `embed()` again on the same id and the existing row is replaced. The entry row is untouched.

This means:
- Searchable text can change after an entry is created without affecting its id, group, or payload.
- An entry can carry only a payload (no FTS, no vec) and still be addressable by id or `(group_key, group_ref)`.
- The same entry can move semantic partitions or change its FTS text without losing its identity.

## Quickstart

```python
from grimoire import grimoire
from grimoire.data.entry import Entry, Filters
from grimoire.embed import FastembedEmbedder

with grimoire.open("grimoire.db", embedder=FastembedEmbedder()) as g:
    [entry] = g.add([
        Entry(
            id=None,
            group_key="creature",
            group_ref="phoenix-001",
            payload={"habitat": "volcano"},
            context="discovered in the southern volcanic chain",
        ),
    ])

    g.keyword([(entry.id, "phoenix fire-bird ashes")])
    g.embed([(entry.id, "A solar phoenix reborn from its own ashes at dawn")])

    for hit in g.semantic_search("creatures that come back from the dead"):
        print(hit.entry.id, hit.distance, hit.entry.semantic_text)

    for hit in g.keyword_search("phoenix"):
        print(hit.entry.id, hit.score, hit.entry.keyword_text)
```

## Imports

The library's surface lives across a few modules. Two common patterns:

```python
# Module-style: pulls in `grimoire.open` and `grimoire.peek`.
from grimoire import grimoire
g = grimoire.open("grimoire.db", embedder=...)
stats = grimoire.peek("grimoire.db")

# Direct: useful when only one helper is needed.
from grimoire.grimoire import open as open_grimoire
g = open_grimoire("grimoire.db", embedder=...)
```

Data types and embedders are imported from their own modules:

```python
from grimoire.data.entry import Entry, Filters, KeywordHit, SemanticHit
from grimoire.embed import Embedder, FastembedEmbedder, NoOpEmbedder
from grimoire.errors import GrimoireError, GrimoireMismatch, GrimoireNotFound, SchemaVersionError
from grimoire.mount import Mount
```

## Public API

### File lifecycle

#### `grimoire.open(path, *, embedder) -> Grimoire`

Open a SQLite file at `path`. An empty (or freshly-touched) file gets the schema installed and the embedder lock written. An initialized file is validated against the supplied embedder; `GrimoireMismatch` is raised on a different `model` or `dimension`. Returns a `Grimoire` ready to use as a context manager.

#### `grimoire.peek(path) -> Peek`

Read metadata and counts from a grimoire file without committing to it for use. Loads sqlite-vec to read the vec partition counts but does not require an embedder. Raises `GrimoireNotFound` if the path doesn't exist or the file lacks an embedder lock.

#### `Grimoire(conn, embedder)`

Direct constructor over an open SQLite connection. `grimoire.open()` is the normal entry point — this is exposed for callers that need to manage the connection themselves.

#### Context manager

`__enter__` returns self; `__exit__` commits on a clean exit and rolls back on an unhandled exception. The idiomatic form is:

```python
with grimoire.open(path, embedder=...) as g:
    ...
```

### Writing entries

#### `add(entries: list[Entry]) -> list[Entry]`

Insert entries. `id` on the input is ignored — a fresh ULID is assigned to each row. Returns the inserted entries with their assigned ids. Raises `ValueError` on a `(group_key, group_ref)` collision with an existing row or within the batch itself.

The embedder is **not** invoked. To make entries searchable, call `keyword()` or `embed()` after `add`.

#### `update(entries: list[Entry]) -> list[Entry]`

Rewrite `group_key`, `group_ref`, `payload`, and `context` on existing rows, identified by `id`. Wholesale: every supplied field replaces the stored value, including with `None`. Returns the entries that matched a row (silently skips ids that didn't). Raises `ValueError` on a `(group_key, group_ref)` collision.

For partial updates, fetch the entry first and replace only the fields you intend to change.

#### `remove(ids: list[str]) -> list[str]`

Delete entries and cascade to their FTS and vec rows. Returns the ids that were actually removed.

### Indexing

#### `keyword(items, *, threshold_rank=None) -> list[Entry]`

Index (or re-index) entries for FTS5 keyword search. `items` is a list of `(entry_id, keyword_text)` tuples. An existing FTS row on the same id is replaced. `threshold_rank` is stored on every row written by this call.

Raises `ValueError` for unknown ids or for empty/whitespace keyword text.

#### `embed(items, *, partition=None, threshold_distance=None) -> list[Entry]`

Embed (or re-embed) entries for semantic search. `items` is a list of `(entry_id, semantic_text)` tuples. Issues one `embed_many` call across the batch. An existing vec row on the same id is replaced — useful for moving an entry to a different partition or updating its source text. `threshold_distance` is stored on every row written by this call.

Raises `ValueError` for unknown ids or for empty/whitespace semantic text.

#### `keyword_remove(ids: list[str]) -> list[str]`

Drop FTS rows for the given ids. Entries themselves are not affected. Returns the ids that had FTS rows.

#### `embed_remove(ids: list[str]) -> list[str]`

Drop vec rows for the given ids. Entries themselves are not affected. Returns the ids that had vec rows.

### Reading

#### `fetch(filters=None, limit=100, cursor=None) -> list[Entry]`

Walk entries ordered by id (i.e. chronologically, since ids are ULIDs). `filters` is a `Filters` instance restricting by sets of `id`, `group_key`, and/or `group_ref`. `cursor`, if given, returns entries with `id > cursor` — pass the last id of the previous page to walk forward.

Each returned `Entry` carries its FTS5 fields (`keyword_text`, `threshold_rank`) and vec0 fields (`semantic_text`, `partition`, `threshold_distance`) inline, populated from a left join. Entries without an index row on either side come back with the corresponding fields set to `None`.

```python
saved = g.fetch(limit=100)
next_page = g.fetch(limit=100, cursor=saved[-1].id)
```

#### `keyword_search(query, filters=None, limit=None) -> list[KeywordHit]`

Run an FTS5 BM25 search against `entry_fts`. `query` is passed straight to FTS5 — phrases (`"exact phrase"`), prefix (`fire*`), boolean operators (`phoenix OR wyrm NOT egg`). Malformed queries surface as `sqlite3.OperationalError`. Filters apply on the joined entry row. Empty/whitespace queries raise `ValueError`.

Returns `KeywordHit`s carrying the matched `entry` and a positive `score` (higher = better). The indexed `keyword_text` and stored `threshold_rank` are available as `hit.entry.keyword_text` and `hit.entry.threshold_rank`.

#### `semantic_search(query, partition=None, limit=10) -> list[SemanticHit]`

Embed `query` via `embedder.embed`, then run vec0 KNN. Pass `partition` to narrow KNN to one partition; omit it (or pass `None`) to span every partition. Returns `SemanticHit`s carrying the matched `entry` and a `distance` (lower = better). The indexed `semantic_text`, `partition`, and stored `threshold_distance` are available as `hit.entry.semantic_text`, `hit.entry.partition`, and `hit.entry.threshold_distance`.

## Data shapes

### `Entry`

```python
@dataclass(frozen=True, slots=True)
class Entry:
    id: str | None        # None on input to `add`; assigned by the library
    group_key: str | None
    group_ref: str | None
    payload: dict[str, Any] | None
    context: str | None = None
    # Index fields, populated by `fetch`, `keyword_search`, `semantic_search`.
    # All None on input to `add`/`update` and ignored — write them via
    # `keyword()` / `embed()` instead.
    keyword_text: str | None = None
    threshold_rank: float | None = None
    semantic_text: str | None = None
    partition: str | None = None
    threshold_distance: float | None = None
```

The five trailing fields are *read-side conveniences*. The entry row in SQLite still holds only the first five fields; the rest are pulled in from `entry_fts` and `entry_vec` via a left join whenever an `Entry` is returned. They're ignored on the way into `add()` and `update()` — use `keyword()` and `embed()` to (re-)write the underlying index rows.

### `Filters`

```python
@dataclass(frozen=True, slots=True)
class Filters:
    id: list[str] | None = None
    group_key: list[str] | None = None
    group_ref: list[str] | None = None
```

Each list, when given, restricts to entries whose field matches one of the listed values. Missing/None means no filter on that field.

### `KeywordHit`

```python
@dataclass(frozen=True, slots=True)
class KeywordHit:
    entry: Entry       # carries `keyword_text` and `threshold_rank` inline
    score: float       # -bm25, so higher = better and non-negative
```

### `SemanticHit`

```python
@dataclass(frozen=True, slots=True)
class SemanticHit:
    entry: Entry       # carries `semantic_text`, `partition`, and `threshold_distance` inline
    distance: float    # vec0 distance, lower = better
```

### `Peek`

```python
@dataclass(frozen=True, slots=True)
class Peek:
    model: str
    dimension: int
    schema_version: int
    entry_count: int
    group_counts: dict[str | None, int]       # by entry.group_key
    partition_counts: dict[str | None, int]   # by entry_vec.partition
```

## Mount

`grimoire.mount.Mount` is a lightweight dataclass that publishes the on-disk layout convention shared with the CLI:

```
<path>/grimoire.db          # default DB
<path>/<name>/grimoire.db   # a named DB
<path>/__models__/          # shared embedder cache
<path>/grimoire.toml        # registry file (reserved, currently inert)
```

```python
from grimoire.mount import Mount, create, destroy

m = Mount(path=Path("/some/dir"))
create(m)                       # idempotent; creates directories and touches the default DB file
m.exists()                      # all of registry + models dir + default DB exist?
m.db_path(None)                 # default DB path
m.db_path("notes")              # named DB path; validates name (lowercase alnum, `-`, `_`, no `__` prefix)
destroy(m)                      # `rm -rf` the entire mount, no undo
```

Names are normalized to lowercase and must match `[a-z0-9_-]+`. Names beginning with `__` are reserved for grimoire's internal directories (`__models__`).

## Custom embedders

`Embedder` is a `Protocol`. Implement four members:

```python
class MyEmbedder:
    @property
    def model(self) -> str: ...
    @property
    def dimension(self) -> int: ...
    def embed(self, text: str) -> list[float]: ...
    def embed_many(self, texts: list[str]) -> list[list[float]]: ...
```

`embed` handles single-record paths (`semantic_search`). `embed_many` handles bulk paths (`embed(items=...)`) and is expected to amortize tokenization, model dispatch, or device transfers across the batch.

The `model` and `dimension` are written into the file on first create and locked. Reopening with a different `model` or `dimension` raises `GrimoireMismatch`.

### Bundled embedders

- **`FastembedEmbedder(model="BAAI/bge-small-en-v1.5", *, cache_folder=None)`** — ONNX-based local inference via `fastembed`. Requires the `fastembed` extra.
- **`NoOpEmbedder`** — produces zero vectors with `model="noop"`, `dimension=1`. For grimoires used only for keyword search, payload storage, or structured browsing — `semantic_search` against a NoOp grimoire returns entries in arbitrary order with distance near zero. The contract is satisfied structurally, but the result has no ranking value.

## Errors

All errors derive from `GrimoireError`:

| Error | Raised when |
|---|---|
| `GrimoireMismatch` | The provided embedder's `model` or `dimension` disagrees with the file's lock. |
| `GrimoireNotFound` | A path was expected to be a grimoire and isn't (missing file, or a SQLite file without an embedder lock). |
| `SchemaVersionError` | The file's `PRAGMA user_version` doesn't match the library's `SCHEMA_VERSION`. Pre-v1, recreate the file. |

## Concurrency

`grimoire.open` opens its SQLite connection in WAL mode with `busy_timeout` defaulted to SQLite's standard. Reads coexist with one writer; sustained high-concurrency writes still serialize at the SQLite level. The connection is bound to its constructing thread per Python's stdlib default.

## Schema notes

Pre-v1, schema changes are not migrated in place. The library checks `PRAGMA user_version` against its expected `SCHEMA_VERSION` on every open; mismatches raise `SchemaVersionError`. The intended response is to recreate the file. Migration ergonomics get designed once v1 is on the table.
