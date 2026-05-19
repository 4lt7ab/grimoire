# 4lt7ab-grimoire-cli

The standalone CLI for grimoire — a single-file semantic datastore backed by SQLite and [`sqlite-vec`](https://github.com/asg017/sqlite-vec). Operates on a mount directory holding one or more grimoire databases, plus an embedded MCP server for AI client integration.

For the Python library, see [`4lt7ab-grimoire`](https://pypi.org/project/4lt7ab-grimoire/).

## Install

```sh
uv tool install '4lt7ab-grimoire-cli[fastembed]'
# or: pipx install '4lt7ab-grimoire-cli[fastembed]'
```

Both install into an isolated venv — clean uninstall, no impact on system Python. The `fastembed` extra pulls the bundled embedder (ONNX-based, no service required).

The `grimoire` command is now on your `PATH`. Confirm the install with `grimoire --version`.

The first `grimoire mount create` (and the first time you reuse the mount on a new machine) fetches the default embedder weights (~30 MB) from HuggingFace into the mount's `__models__/` cache. Subsequent runs reuse the cache and stay offline. On macOS, if the fetch stalls on TLS errors, set `HF_HUB_DISABLE_XET=1` once to bypass the xet CDN.

## Mount model

A **mount** is a directory containing one or more grimoire databases plus a shared embedder model cache:

```
<mount>/
├── grimoire.db            # the default DB
├── <name>/
│   └── grimoire.db        # a named DB
├── __models__/            # shared embedder cache
└── grimoire.toml          # registry file (reserved, currently inert)
```

The mount resolves in this order: `--mount <dir>` flag > `GRIMOIRE_MOUNT` env var > `~/.grimoire`.

Set the env var once per shell to avoid passing `--mount` everywhere:

```sh
export GRIMOIRE_MOUNT=$PWD/.grimoire
```

A mount can hold one **default database** at `<mount>/grimoire.db` plus any number of **named databases** under `<mount>/<name>/grimoire.db`. Pick which one a command targets with `--db <name>` / `-d <name>`; omit `--db` to target the default. Names must match `[a-z0-9_-]+` and cannot begin with `__` (reserved).

## Mental model

A grimoire entry has two layers:

- The **entry** itself: `uniq_id` (library-assigned ULID) + `data` (JSON blob).
- Three opt-in **sidecars** keyed by that `uniq_id`:
  - **`entry_idx`** — filterable columns: `uniq_ref` (external reference) and five symmetric `ordinal_1`..`ordinal_5` slots (BLOB-affinity — store any scalar: numbers, strings, labels).
  - **`entry_fts`** — FTS5 keyword text.
  - **`entry_vec`** — semantic vector + source text.

`entry add` creates an entry and optionally writes any subset of the three sidecars in the same call. Sidecar writes are **PUT** — supplying any of `--ref`/`--ord-*` wholesale-replaces the `entry_idx` row; supplying `--match` replaces the FTS row; supplying `--search` replaces the vec row. Deleting an entry cascade-cleans every sidecar via a DB trigger.

## Quickstart

```sh
export GRIMOIRE_MOUNT=$PWD/.grimoire

# Create the mount + default DB. Idempotent.
grimoire mount create

# Add an entry with data, idx metadata, keyword text, and semantic text.
# Each idx/match/search flag is independent — pick any subset.
grimoire entry add \
    --data '{"habitat": "volcano"}' \
    --ref phoenix-001 \
    --ord-1 creature \
    --ord-2 1.0 \
    --match "phoenix fire-bird ashes" \
    --search "A solar phoenix reborn from its own ashes at dawn"

# Search.
grimoire search "creatures that come back from the dead"
grimoire match "phoenix"

# Browse entry_idx rows with filters.
grimoire query --equals ordinal_1=creature --gte ordinal_2=0.5

# Look up entries by external reference.
grimoire fetch phoenix-001

# Inspect the database — model, dimension, schema version, per-table counts, file size.
grimoire info
```

Every command prints pretty-indented JSON. Pipe through `jq` for filtering and extraction.

## Commands

### Global options

| Option | Behavior |
|---|---|
| `--mount <dir>` | Override the mount path for this invocation. Precedence: `--mount` > `$GRIMOIRE_MOUNT` > `~/.grimoire`. |
| `--version` | Print the CLI version and exit. |
| `--help` | Print help for the command (or subcommand) and exit. |

### Mount administration

#### `grimoire mount create`

Create the mount directory, shared `__models__/` cache, and the default database. Idempotent — safe to re-run. Loads the default embedder on first create to write the embedder lock.

#### `grimoire mount destroy --yes`

Wipe the entire mount: every database, the model cache, the registry. There is no undo. `--yes` is required.

#### `grimoire mount add <name>`

Create a named database in the mount. The mount itself must already exist (run `mount create` first).

#### `grimoire mount ls`

List databases in the mount as a JSON array of `{"db": <str|null>, "path": <str>}`. The default DB appears first with `db: null`; named DBs follow alphabetically.

#### `grimoire mount remove <name> --yes`

Delete a single named database file from the mount. The model cache and other databases are preserved. `--yes` is required.

### Database inspection

#### `grimoire info [--db <name>]`

Show metadata for a database: embedder lock (`model`, `dimension`), `schema_version`, per-table row counts (`entry_count`, `entry_idx_count`, `entry_fts_count`, `entry_vec_count`), file path, file size. Does not load the embedder.

#### `grimoire analyze [--db <name>]`

Run SQLite's `ANALYZE` to refresh `sqlite_stat1`. The rotation composite indexes on `entry_idx` rely on accurate selectivity stats for the planner to pick among them — run after bulk loads or whenever the data distribution shifts. Prints `{"db": <name|null>, "analyzed": true}` on success.

### Entry CRUD

#### `grimoire entry add [options]`

Create an entry and optionally PUT-index its sidecars in one call.

| Option | Behavior |
|---|---|
| `--db`, `-d` | Target a named DB. Omit for the default. |
| `--data` | JSON value stored in `entry.data` (object, array, scalar, or null). |
| `--ref` | `entry_idx.uniq_ref` value. |
| `--ord-1` .. `--ord-5` | `entry_idx.ordinal_N` values. Each value is coerced int → float → string, so a numeric literal stores as a number and anything else stores as text. |
| `--match` | Text written to the FTS5 row. PUT-replaces the entry's `entry_fts` row. |
| `--search` | Text embedded via the bundled embedder. PUT-replaces the entry's `entry_vec` row. |

Supplying any of `--ref` or `--ord-*` PUT-replaces the entry's `entry_idx` row; omitted columns become NULL. Omit them all to leave `entry_idx` untouched.

#### `grimoire entry update <uniq_id> [options]`

Update entry data and/or PUT-index sidecars. Omit `--data` to leave the data column untouched. Sidecar flags follow the same PUT semantics as `entry add`.

#### `grimoire entry get <uniq_id> [<uniq_id>...]`

Fetch one or more entries by `uniq_id`. Returns a JSON array of entry objects.

#### `grimoire entry remove <uniq_id> --yes`

Remove an entry. Sidecar rows are cascade-cleaned by DB trigger. `--yes` is required. Returns `{"uniq_id": <id>, "removed": <bool>}` — `removed=false` if the id did not exist.

### Reads

All four read commands return paired results: each match is `{"entry": {...}, "<key>": ...}`, where `<key>` is `"index"`, `"score"`, or `"distance"` depending on the command.

#### `grimoire query [filters] [--cursor <id>] [--limit <n>]`

Browse `entry_idx` rows ordered by `uniq_id` ASC, joined to `entry` for the data side. Returns `[{"entry": {...}, "index": {...}}, ...]`.

| Option | Behavior |
|---|---|
| `--db`, `-d` | Target a named DB. |
| `--equals KEY=VALUE` | Filter `entry_idx.<KEY> IN (...)`. Repeatable. Valid keys: any `entry_idx` column. Value is coerced int → float → string. |
| `--gte KEY=VALUE` | Filter `entry_idx.<KEY> >= VALUE`. Repeatable. Valid keys: `ordinal_1`..`ordinal_5`. Same coercion as `--equals`. |
| `--lte KEY=VALUE` | Filter `entry_idx.<KEY> <= VALUE`. Repeatable. |
| `--cursor` | Return rows with `uniq_id > <cursor>`. Pass the last id of the previous page. |
| `--limit` | Maximum rows (default 100). |

```sh
LAST=$(grimoire query --limit 100 | jq -r '.[-1].entry.uniq_id')
grimoire query --limit 100 --cursor "$LAST"
```

#### `grimoire fetch <uniq_ref> [<uniq_ref>...]`

Fetch entries whose `entry_idx.uniq_ref` is in the given list. Returns `[{"entry": {...}, "index": {...}}, ...]`. `uniq_ref` is sparse-unique (UNIQUE partial index over the non-NULL rows), so each ref maps to at most one entry; entries without an `entry_idx` row are invisible to `fetch`.

#### `grimoire match <query> [filters] [--limit <n>]`

FTS5 BM25 keyword search. Free-form prose is tokenized into safe FTS5 syntax automatically: each word token is quoted and joined with `OR`, so apostrophes, punctuation, and bareword FTS5 operators (`AND`, `OR`, `NOT`, `NEAR`, `*`) in the query can't reach the parser.

| Option | Behavior |
|---|---|
| `--db`, `-d` | Target a named DB. |
| `--equals KEY=VAL`, `--gte KEY=NUMBER`, `--lte KEY=NUMBER` | Apply `entry_idx` filters via JOIN. Same shape as `query`. |
| `--limit` | Maximum hits (default 10). |

Returns `[{"entry": {...}, "score": <bm25>}, ...]`. `score` is positive (higher = better).

#### `grimoire search <query> [--limit <n>]`

vec0 KNN semantic search. Embeds the query via the bundled embedder, then ranks by vector distance.

| Option | Behavior |
|---|---|
| `--db`, `-d` | Target a named DB. |
| `--limit` | Maximum hits (default 10). |

Returns `[{"entry": {...}, "distance": <float>}, ...]`. `distance` is the raw vec0 distance (lower = better, non-negative).

### MCP server

#### `grimoire mcp serve`

Run a FastMCP server over stdio, scoped to this mount. Wires the library's read+write surface as MCP tools an AI client can call directly. Mount administration (`mount create/destroy/add/remove`) stays CLI-only.

Tools exposed: `info`, `add`, `update`, `get`, `remove`, `query`, `fetch`, `match`, `search`. `add` and `update` both accept the data + idx + match + search kwargs (mirroring the CLI's `entry add`/`entry update`). For MCP `update`, passing `data: null` (or omitting it) leaves the data column alone — there's no way to MCP-update an entry's data to NULL because JSON can't distinguish "passed null" from "not passed."

## Output format

Every command prints pretty-indented JSON to stdout. There is no table mode, no `--raw`, no auto-detect — JSON in, JSON out, pipe to `jq` to slice. Errors go to stderr via Typer's standard messaging.

## Environment variables

| Variable | Default | Behavior |
|---|---|---|
| `GRIMOIRE_MOUNT` | `~/.grimoire` | Mount directory. Overridden by `--mount`. |
| `GRIMOIRE_TELEMETRY` | `off` | Telemetry sink wired into every `Grimoire.open()`, including the in-process MCP server. `off` drops everything; `logging` enables stdlib `logging` (INFO records per span/event with structured fields under `extra={"grimoire": {...}}`). Records go to stderr so they don't interfere with JSON-on-stdout. |

## Schema notes

Pre-v1, schema changes are not migrated in place. The library checks `PRAGMA user_version` against its expected `SCHEMA_VERSION` on every open; mismatches raise `SchemaVersionError`. Recreate the file (and re-index its contents) when this happens. Migration ergonomics get designed once v1 is on the table.

## Uninstall

```sh
uv tool uninstall 4lt7ab-grimoire-cli
# or: pipx uninstall 4lt7ab-grimoire-cli
```

The mount directory at `~/.grimoire` (or wherever you pointed `GRIMOIRE_MOUNT`) is left intact. Remove it manually with `grimoire mount destroy --yes` *before* uninstalling, or `rm -rf` after.
