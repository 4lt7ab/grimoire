# 4lt7ab-grimoire

The Python library behind grimoire — a single-file semantic datastore backed by SQLite and [`sqlite-vec`](https://github.com/asg017/sqlite-vec). Entries are bare `(uniq_id, data)` rows; three opt-in sidecars attach typed filterable metadata, FTS5 keyword search, and vec0 semantic search.

For the standalone CLI, see [`4lt7ab-grimoire-cli`](https://pypi.org/project/4lt7ab-grimoire-cli/).

## Install

```sh
uv add '4lt7ab-grimoire[fastembed]'
```

The `fastembed` extra pulls the bundled `FastembedEmbedder` (ONNX-based, no service required). Drop the extra and implement the `Embedder` protocol to bring your own — see [Custom embedders](#custom-embedders).

## Mental model

A grimoire is a single SQLite file with four tables plus a meta row:

- **`entry`** — `(uniq_id, data)`. Identity row. `uniq_id` is a ULID; `data` is a JSON-serializable value (object, array, scalar, or null).
- **`entry_idx`** — typed filterable/sortable metadata sidecar keyed by `uniq_id`: `uniq_ref` (TEXT), `nominal_1`, `nominal_2` (TEXT), `ordinal_1`, `ordinal_2`, `ordinal_3` (REAL). All columns nullable.
- **`entry_fts`** — FTS5 keyword text sidecar keyed by `uniq_id`.
- **`entry_vec`** — vec0 semantic sidecar keyed by `uniq_id`: source text + embedding.

Plus a `meta` table that records the embedder's `model` and `dimension` at create time. Reopening with a mismatched embedder raises `GrimoireMismatch`.

**Sidecars are decoupled from entry creation.** `add()` writes only the entry row. To make an entry searchable or filterable, call `index()` with the kwargs for the sides you want populated. An entry can have a row in zero, one, two, or all three of the sidecars.

**Deletion cascades automatically.** Removing an entry fires an `AFTER DELETE` trigger that cleans up any rows in `entry_idx`, `entry_fts`, and `entry_vec` for that `uniq_id`. No `*_remove` helpers — the entry's identity row is the only thing to delete.

**Re-indexing is wholesale (PUT-style).** `index()` replaces the sidecar rows you touch. Omit a kwarg and that sidecar is left alone; pass it and the sidecar is overwritten end-to-end. For the entry_idx side, that means passing `ref="X"` alone wipes any existing `nominal_*`/`ordinal_*` columns.

## Quickstart

```python
from grimoire.grimoire import Grimoire
from grimoire.data.entry import Entry, Filters
from grimoire.embed import FastembedEmbedder

with Grimoire.open("grimoire.db", embedder=FastembedEmbedder()) as g:
    [entry] = g.add([Entry(uniq_id=None, data={"habitat": "volcano"})])

    g.index(
        entry.uniq_id,
        ref="phoenix-001",
        nom=("creature", None),
        ord=(1.0, None, None),
        match="phoenix fire-bird ashes",
        search="A solar phoenix reborn from its own ashes at dawn",
    )

    # Browse the idx side with filters; each entry comes back with its idx row.
    entries, indexes = g.query(Filters(equals={"nominal_1": ["creature"]}))
    for e, i in zip(entries, indexes, strict=True):
        print(e.uniq_id, e.data, i.uniq_ref)

    # Semantic search; each hit comes back with its entry.
    entries, hits = g.search("creatures that come back from the dead")
    for e, h in zip(entries, hits, strict=True):
        print(h.distance, e.data)
```

## Imports

```python
from grimoire.grimoire import Grimoire
from grimoire.data.entry import Entry, EntryIndex, Filters, KeywordHit, SemanticHit
from grimoire.embed import Embedder, FastembedEmbedder, NoOpEmbedder
from grimoire.errors import (
    GrimoireError, GrimoireMismatch, GrimoireNotFound,
    EmbedderRequired, SchemaVersionError,
)
from grimoire.mount import Mount
```

## Public API

### File lifecycle

#### `Grimoire.open(path, *, embedder=None, check_same_thread=True) -> Grimoire`

Open or initialize a SQLite file at `path`. An empty file gets the schema installed and the embedder lock written. An initialized file is validated against the supplied embedder; mismatched `model` or `dimension` raises `GrimoireMismatch`.

Without an embedder, an empty file locks to NoOp sentinel values (`model="noop"`, `dimension=1`). The lock is sticky: reopening with a real embedder later raises `GrimoireMismatch`. Semantic operations on a NoOp-locked grimoire raise `EmbedderRequired`.

`check_same_thread` is forwarded to `sqlite3.connect`. Pass `False` to use the returned `Grimoire` from a different thread (you serialize access).

#### `Grimoire.peek(path, *, check_same_thread=True) -> Peek`

Inspect a file without binding an embedder. Returns `model`, `dimension`, `schema_version`, and per-sidecar row counts. Raises `GrimoireNotFound` if the path doesn't exist or has no embedder lock.

#### `Grimoire(conn, embedder=None)`

Direct constructor over an open SQLite connection. `Grimoire.open()` is the normal entry point.

#### Context manager

`__enter__` returns self; `__exit__` commits on a clean exit and rolls back on an unhandled exception.

```python
with Grimoire.open(path, embedder=...) as g:
    ...
```

### entry  (identity table)

#### `add(entries: list[Entry]) -> list[Entry]`

Insert entries. `uniq_id` on input is ignored — a fresh ULID is assigned to each row. Returns the inserted entries with their assigned ids. The embedder is **not** invoked — to make entries searchable, call `index()` after.

#### `update(entries: list[Entry]) -> list[Entry]`

Rewrite the `data` column on existing rows, keyed by `uniq_id`. Returns the entries that matched a row (silently skips unknown ids). Sidecars are untouched — use `index()` for those.

#### `remove(uniq_ids: list[str]) -> list[str]`

Delete entries. The `AFTER DELETE` trigger cascade-cleans any matching rows in `entry_idx`, `entry_fts`, and `entry_vec`. Returns the ids that were actually deleted.

#### `get(uniq_ids: list[str]) -> list[Entry]`

Fetch entries by `uniq_id`. Returns only the ones that exist; no order guarantee.

#### `fetch(uniq_refs: list[str]) -> tuple[list[Entry], list[EntryIndex]]`

Fetch entries whose `entry_idx` row has `uniq_ref` in the given list. Returns parallel `(entries, indexes)` lists; `entries[i]` corresponds to `indexes[i]`. Entries without an `entry_idx` row are invisible to `fetch()` (no idx, no ref to match). Multiple entries may share a `uniq_ref` (no uniqueness constraint).

### index  (combined sidecar writer)

#### `index(uniq_id, *, ref=None, ord=None, nom=None, match=None, search=None) -> None`

One-shot PUT across the three sidecars for a single entry. Each kwarg writes wholesale; no reads, no merging.

- `ref`, `ord`, `nom` together describe the `entry_idx` row. If any one is supplied, the row is fully replaced; columns mapped to unsupplied positions (or `None` inside a tuple) become NULL. Omit all three to leave `entry_idx` untouched. `ord` is a 3-tuple, `nom` is a 2-tuple.
- `match` replaces the `entry_fts` row with this text.
- `search` embeds the text via the bound embedder and replaces the `entry_vec` row. Raises `EmbedderRequired` if the grimoire was opened without one.

`uniq_id` must reference an existing entry; otherwise the underlying sidecar writes raise `ValueError`.

### query  (entry_idx browse)

#### `query(filters=None, limit=100, cursor=None) -> tuple[list[Entry], list[EntryIndex]]`

Walk entry_idx rows ordered by `uniq_id` ASC, joined to entry for the data side. Returns parallel `(entries, indexes)` lists.

`cursor`, if given, returns rows with `uniq_id > cursor`. For ordinal-window paging, use `Filters(gte={...}, lte={...})`.

### match  (FTS5 keyword search)

#### `match(query, filters=None, limit=None) -> tuple[list[Entry], list[KeywordHit]]`

FTS5 BM25 search. `query` is passed straight to FTS5 — phrases (`"exact phrase"`), prefix (`fire*`), boolean operators (`phoenix OR wyrm NOT egg`). Malformed queries surface as `sqlite3.OperationalError`. Filters apply via JOIN to `entry_idx`. Empty/whitespace queries raise `ValueError`.

Returns parallel `(entries, hits)` lists in BM25 rank order. `KeywordHit.score` is positive (higher = better).

### search  (vec0 semantic KNN)

#### `search(query, limit=10) -> tuple[list[Entry], list[SemanticHit]]`

Embed `query` via the bound embedder, then run vec0 KNN. Returns parallel `(entries, hits)` lists nearest-first by distance. `SemanticHit.distance` is the raw vec0 distance (lower = better). Raises `EmbedderRequired` if the grimoire was opened without an embedder.

## Data shapes

### `Entry`

```python
@dataclass(frozen=True, slots=True)
class Entry:
    uniq_id: str | None    # None on input to `add`; assigned by the library
    data: Any = None       # any JSON-serializable value
```

### `EntryIndex`

```python
@dataclass(frozen=True, slots=True)
class EntryIndex:
    uniq_id: str | None
    uniq_ref: str | None = None
    nominal_1: str | None = None
    nominal_2: str | None = None
    ordinal_1: float | None = None
    ordinal_2: float | None = None
    ordinal_3: float | None = None
```

Read-side type returned by `query` and `fetch`. The library writes via `index(uniq_id, ref=..., ord=(...), nom=(...))`; you only construct `EntryIndex` instances yourself if you're reaching past the public surface.

### `Filters`

```python
@dataclass(frozen=True, slots=True)
class Filters:
    equals: dict[str, list[Any]] | None = None
    gte: dict[str, float] | None = None
    lte: dict[str, float] | None = None
```

`equals` keys may name any of the seven `entry_idx` columns. `gte` / `lte` keys must name one of `ordinal_1`/`ordinal_2`/`ordinal_3`. Unknown columns raise `ValueError`. Empty lists in `equals` skip the filter (no-op).

### `KeywordHit`

```python
@dataclass(frozen=True, slots=True)
class KeywordHit:
    uniq_id: str
    score: float       # -bm25, so higher = better and non-negative
```

### `SemanticHit`

```python
@dataclass(frozen=True, slots=True)
class SemanticHit:
    uniq_id: str
    distance: float    # vec0 distance, lower = better, non-negative
```

### `Peek`

```python
@dataclass(frozen=True, slots=True)
class Peek:
    model: str
    dimension: int
    schema_version: int
    entry_count: int
    entry_idx_count: int
    entry_fts_count: int
    entry_vec_count: int
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

Names are normalized to lowercase and must match `[a-z0-9_-]+`. Names beginning with `__` are reserved.

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

`embed` handles single-record paths (`search`, `index(..., search=...)`). `embed_many` is reserved for future batch paths.

The `model` and `dimension` are written into the file on first create and locked. Reopening with a different `model` or `dimension` raises `GrimoireMismatch`.

### Bundled embedders

- **`FastembedEmbedder(model="BAAI/bge-small-en-v1.5", *, cache_folder=None)`** — ONNX-based local inference via `fastembed`. Requires the `fastembed` extra.
- **`NoOpEmbedder`** — produces zero vectors with `model="noop"`, `dimension=1`. For grimoires used only for keyword search or structured data — `search` against a NoOp grimoire returns entries in arbitrary order with distance near zero. The contract is satisfied structurally, but the result has no ranking value.

## Errors

All errors derive from `GrimoireError`:

| Error | Raised when |
|---|---|
| `GrimoireMismatch` | The provided embedder's `model` or `dimension` disagrees with the file's lock. |
| `GrimoireNotFound` | A path was expected to be a grimoire and isn't (missing file, or a SQLite file without an embedder lock). |
| `EmbedderRequired` | A semantic operation (`search` or `index(..., search=...)`) ran on a grimoire opened without an embedder. |
| `SchemaVersionError` | The file's `PRAGMA user_version` doesn't match the library's `SCHEMA_VERSION`. Pre-v1, recreate the file. |

## Concurrency

`Grimoire.open` opens its SQLite connection with `busy_timeout` defaulted to SQLite's standard. Reads coexist with one writer; sustained high-concurrency writes serialize at the SQLite level. The connection is bound to its constructing thread per Python's stdlib default; pass `check_same_thread=False` to lift that restriction (and serialize access yourself).

## Schema notes

Pre-v1, schema changes are not migrated in place. The library checks `PRAGMA user_version` against its expected `SCHEMA_VERSION` on every open; mismatches raise `SchemaVersionError`. Recreate the file when this happens. Migration ergonomics get designed once v1 is on the table.
