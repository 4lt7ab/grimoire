# 4lt7ab-grimoire

The Python library behind grimoire — a single-file semantic datastore backed by SQLite and [`sqlite-vec`](https://github.com/asg017/sqlite-vec). Add records, query them by meaning or keyword, get back what they were pointing at.

For the standalone CLI, see [`4lt7ab-grimoire-cli`](https://pypi.org/project/4lt7ab-grimoire-cli/).

## Install

```sh
uv add '4lt7ab-grimoire[fastembed]'
```

The `fastembed` extra pulls the bundled `FastembedEmbedder` (ONNX-based, no service required). To bring your own embedder, drop the extra and implement the `Embedder` protocol — see [Custom embedders](#custom-embedders).

## Mental model

A grimoire is a single SQLite file. Everything lives inside it: the entries, the vector index, the keyword (FTS5) index, and a one-row lock table that records the embedder's `model` and `dimension` at create time. Reopening the file with a mismatched embedder raises `GrimoireMismatch` rather than producing nonsense vectors.

An entry has up to four content slots, all optional and independent:

- **`vector_text`** — free-form text the embedder vectorizes. Entries without it are invisible to `vector_search` but still retrievable by id, group, or list.
- **`keyword_text`** — free-form text indexed for BM25 keyword search. Entries without it are invisible to `keyword_search`.
- **`payload`** — an arbitrary JSON object. The structured thing the text was pointing at. Returned alongside every search hit.
- **`threshold`** — an optional per-entry similarity gate, applied when `vector_search(..., dynamic_threshold=True)`.

Plus identity:

- **`id`** — a ULID assigned by grimoire. Sorts lexicographically by creation time, doubles as a pagination cursor.
- **`group_key`** — optional group label. Vector index partitions on it; filtered searches push the filter into the index rather than scanning.
- **`group_ref`** — optional consumer-set identifier. `(group_key, group_ref)` is enforced unique when set; reuse the same `group_ref` across different `group_key`s freely.

**Entries are mostly immutable.** Once created, the indexed and identity fields (`vector_text`, `keyword_text`, `group_key`, `group_ref`) cannot change. Only `payload` and `threshold` can be patched in place. To "rename" or re-embed an entry, delete it and add a fresh one — `group_ref` gives you stable external identity across the swap.

## Quickstart

```python
from grimoire import Grimoire
from grimoire.embedders import FastembedEmbedder

embedder = FastembedEmbedder(cache_folder=".grimoire/models")

with Grimoire.create(embedder=embedder, mount=".grimoire") as g:
    g.add(
        group_key="creature",
        group_ref="phoenix-001",
        vector_text="A solar phoenix reborn from its own ashes at dawn",
        keyword_text="phoenix fire-bird ashes",
        payload={"habitat": "volcano"},
    )
    g.add(
        group_key="creature",
        group_ref="wyrm-014",
        vector_text="An ancient wyrm hoarding obsidian in the Ash Peaks",
        payload={"habitat": "mountain"},
        threshold=0.5,
    )

    for entry in g.vector_search("creatures that come back from the dead", k=5):
        print(entry.id, entry.distance, entry.vector_text, entry.payload)

    for entry in g.keyword_search("phoenix", k=5):
        print(entry.id, entry.rank, entry.keyword_text, entry.payload)
```

## Public API

### `Grimoire` — file lifecycle

`Grimoire` is the single entry point. Every operation is a method on it (or a classmethod on the type).

#### `Grimoire.create(name=None, *, embedder, mount=None, description=None, check_same_thread=True) -> Grimoire`

Create a new database in the mount and return an open handle. `name=None` creates the default at `<mount>/grimoire.db`; a name creates `<mount>/<name>/grimoire.db` and registers it in the manifest. Raises `DatabaseExists` if a database with this name is already present — use `Grimoire.open` to attach to an existing one. Resolves the mount in this order: explicit `mount=` arg, `GRIMOIRE_MOUNT` env var, `~/.grimoire`. Creates the mount root and shared `models/` cache on demand.

#### `Grimoire.open(name=None, *, mount=None, check_same_thread=True) -> Grimoire`

Open an existing database in the mount. Auto-loads the embedder using the file's locked `model` name and the mount's shared `models/` cache via `FastembedEmbedder` — requires the `fastembed` extra. Raises `GrimoireNotFound` if the file is missing, `GrimoireMismatch` if the file's lock disagrees with the auto-loaded embedder. For custom embedders that don't round-trip through a string name, open the file directly without the mount layer.

#### `Grimoire.destroy(name=None, *, mount=None) -> None`

Delete a single database from the mount, including its WAL/SHM siblings. Drops the manifest entry for named DBs and best-effort removes the now-empty subdirectory. Idempotent — missing files are tolerated.

#### `Grimoire.peek(path) -> Stats | None`

Read metadata and counts from a grimoire file without opening it for use. Returns `None` if the file does not exist or is not a grimoire. Does not load `sqlite-vec` or require an embedder, so it is safe for inspection (CLI `info`, model auto-detect) before deciding how to open.

#### `Grimoire.mount(path=None) -> Mount`

Resolve and prepare a mount, returning a `Mount` handle for mount-level operations (listing databases, peeking at them without opening, destroying the whole mount). See [`Mount`](#mount--directory-of-databases) below.

#### `close() -> None` / context manager

Close the underlying SQLite connection. The `__enter__`/`__exit__` protocol calls `close()` on exit, so the idiomatic form is `with Grimoire.create(...) as g:`.

### Writing entries

#### `add(*, vector_text=None, keyword_text=None, group_key=None, group_ref=None, payload=None, threshold=None) -> Entry`

Insert a single entry. `vector_text` and `keyword_text` are independent — pass either, both, or neither. The embedder is invoked only when `vector_text` is set; the FTS index gets a row only when `keyword_text` is set. Returns the inserted `Entry` with its grimoire-assigned `id`. Raises `sqlite3.IntegrityError` on `(group_key, group_ref)` collision.

#### `add_many(records: Iterable[Mapping[str, Any]]) -> list[Entry]`

Insert many records in one transaction with one batched embed call. Each record is a mapping accepting the same keys as `add`'s kwargs — all optional. The embedder is called once across only those records that supplied a `vector_text`; records without it skip vec0 entirely. Same for `keyword_text` and the FTS index. Atomic: if embedding or any insert fails, nothing is committed — unlike a loop over `add`, which would leave partial state behind.

### Reading entries

#### `get(entry_id: str) -> Entry | None`

Fetch an entry by id, or `None` if no match.

#### `get_by_group_ref(*, group_key: str | None, group_ref: str) -> Entry | None`

Fetch an entry by its consumer-set `(group_key, group_ref)` composite. `group_key=None` looks up an entry in the global (ungrouped) namespace. Returns `None` if no match.

#### `list(*, group_key=None, group_ref=None, limit=100, after_id=None, created_after=None, created_before=None) -> list[Entry]`

Chronological pagination over entries. The id IS the cursor — pass the last id of the previous page to `after_id` to walk forward. ULIDs sort lexicographically by creation time, so this walks pages in chronological order without a separate cursor type. `created_after`/`created_before` accept `datetime` objects and translate to ULID range bounds.

#### `vector_search(query, *, group_key=None, k=10, dynamic_threshold=False, created_after=None, created_before=None) -> list[Entry]`

Return up to `k` entries ranked by vector distance to `query`. Result `Entry` objects carry a `distance` field.

Filters interact with the KNN in two different ways:

- `group_key` is pushed into the vector index's partition key, so the KNN considers only entries of that `group_key` from the start.
- `created_after`, `created_before`, and `dynamic_threshold` apply **after** the KNN returns its top-k. With a narrow time window or tight per-record thresholds, this can return fewer than `k` results — even when many qualifying entries exist further down the similarity ranking. Raise `k` to compensate.

`dynamic_threshold=True` keeps only entries whose stored `threshold` is `None` or whose `distance` is at or below it.

#### `keyword_search(query, *, group_key=None, k=10, created_after=None, created_before=None) -> list[Entry]`

Return up to `k` entries ranked by SQLite FTS5 BM25 against `keyword_text`. Result `Entry` objects carry a `rank` field (FTS5 BM25 returns negative scores; smaller is better, and the result list is already sorted). The query string accepts FTS5 syntax — phrases (`"exact phrase"`), prefix (`fire*`), boolean operators (`phoenix OR wyrm NOT egg`).

### Mutation

#### `update(entry_id, *, payload=_UNSET, threshold=_UNSET) -> Entry | None`

Patch the mutable metadata fields on an entry. **Only `payload` and `threshold` can be updated** — the indexed and identity fields (`vector_text`, `keyword_text`, `group_key`, `group_ref`) are immutable after creation. Passing them raises `TypeError`. To change them, `delete` and `add` again.

Omit a field to leave it alone; pass `None` to clear it; pass a value to replace it. The single SQL `UPDATE` never re-embeds, never rewrites the FTS index, never moves vec0 partitions. Returns the updated `Entry`, or `None` if the id is unknown.

#### `delete(entry_id: str) -> bool`

Delete an entry and its index rows. Returns `True` if the entry existed, `False` otherwise.

#### `delete_many(ids: Iterable[str]) -> list[bool]`

Delete many entries in one transaction. Returns one `bool` per input id in input order — `True` if the entry existed and was deleted, `False` otherwise. Duplicate ids each receive the same answer (their pre-call existence). Atomic: all successful deletes apply or none do.

## Mount — directory of databases

A mount is a directory containing one or more grimoire databases plus a shared embedder model cache:

```
<mount>/
├── grimoire.toml          # registry of named DBs (lazy)
├── models/                # shared embedder cache
├── grimoire.db            # the default DB
└── <name>/
    └── grimoire.db        # a named DB
```

Get a `Mount` handle via `Grimoire.mount(path=None)`. The path resolves with the same precedence as `Grimoire.create`: explicit arg > `GRIMOIRE_MOUNT` > `~/.grimoire`.

| Method | Behavior |
|---|---|
| `path` *(property)* | The mount's resolved root path. |
| `path_for(name)` | The SQLite path for `name` in this mount (`None` for the default). |
| `has(name)` | True if a database with this name exists. |
| `peek(name)` | `Stats` for the database, or `None` if missing. Doesn't load an embedder. |
| `list()` | `list[DbInfo]` — default DB first, then named DBs alphabetically. Manifest entries with missing files are silently skipped. |
| `destroy()` | Delete the entire mount directory. Idempotent on missing paths. After this call, every other method on the handle raises `MountDestroyed`. |

## Inspecting without opening

```python
from grimoire import Grimoire

stats = Grimoire.peek(".grimoire/grimoire.db")
if stats:
    print(stats.model, stats.dimension, stats.entry_count, stats.groups)
```

`Grimoire.peek` does not load `sqlite-vec` or require an embedder — safe to call against any path before you commit to opening it.

## Data shapes

### `Entry`

```python
@dataclass
class Entry:
    id: str
    vector_text: str | None = None
    keyword_text: str | None = None
    group_key: str | None = None
    group_ref: str | None = None
    payload: dict[str, Any] | None = None
    threshold: float | None = None
    distance: float | None = None  # set by vector_search
    rank: float | None = None      # set by keyword_search

    @property
    def created_at(self) -> datetime: ...  # derived from id
```

`distance` and `rank` are populated only on results from the matching search method; they're `None` everywhere else.

### `Stats`

```python
@dataclass
class Stats:
    model: str
    dimension: int
    schema_version: int
    entry_count: int
    groups: dict[str, int]
```

### `DbInfo`

```python
@dataclass
class DbInfo:
    name: str | None  # None for the default DB
    path: Path
    model: str
    dimension: int
    entry_count: int
    is_default: bool
```

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

`embed` handles single-record paths (`add`, `vector_search`). `embed_many` handles bulk paths (`add_many`) and is expected to amortize tokenization, model dispatch, or device transfers across the batch — most embedding libraries do this naturally.

The `model` and `dimension` are written into the file on first create and locked. Reopening with a different `model` or `dimension` raises `GrimoireMismatch`.

## Errors

All errors derive from `GrimoireError`:

| Error | Raised when |
|---|---|
| `GrimoireMismatch` | The provided embedder's `model` or `dimension` disagrees with the file's lock. |
| `GrimoireNotFound` | A path was expected to be a grimoire and isn't (missing file, or a SQLite file without the lock table). |
| `SchemaVersionError` | The file's `PRAGMA user_version` doesn't match the library's expected `SCHEMA_VERSION`. Pre-v1, this means delete and re-init. |
| `InvalidEmbedder` | An embedder reports a non-int or non-positive `dimension`, or a non-string / empty `model`. |
| `DatabaseExists` | `Grimoire.create` was called against a path that already has a database. |
| `InvalidMount` | A mount path or named-DB name is malformed or reserved (e.g. names like `models`, `grimoire.toml`, `grimoire.db`, anything starting with `.` or containing path separators). |
| `MountDestroyed` | A method was called on a `Mount` handle whose `destroy()` has already been called. |

## Concurrency

`Grimoire` opens its SQLite connection in WAL mode with `busy_timeout = 5000`, so readers coexist with one writer and occasional multi-writer attempts queue at the SQLite level rather than crash with `database is locked`. Sustained high-concurrency writes still serialize.

By default the connection is bound to its constructing thread (SQLite's `check_same_thread=True`). For workloads where the grimoire is opened on one thread and used on another (e.g. FastAPI sync handlers in asyncio's default executor), pass `check_same_thread=False` to `Grimoire.create` / `Grimoire.open`.

## Schema notes

Pre-v1, schema changes are not migrated in place. The file's `PRAGMA user_version` is checked against the library's `SCHEMA_VERSION` on `open`; mismatches raise `SchemaVersionError`. The intended response is to delete the file and re-init from JSONL exports. Migration ergonomics get designed once v1 is on the table.
