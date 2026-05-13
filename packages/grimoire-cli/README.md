# 4lt7ab-grimoire-cli

The standalone CLI for grimoire — a single-file semantic datastore backed by SQLite and [`sqlite-vec`](https://github.com/asg017/sqlite-vec). Operates on a mount directory holding one or more grimoire databases, plus an embedded MCP server for AI client integration.

For the Python library, see [`4lt7ab-grimoire`](https://pypi.org/project/4lt7ab-grimoire/).

## Install

```sh
uv tool install '4lt7ab-grimoire-cli[fastembed]'
# or: pipx install '4lt7ab-grimoire-cli[fastembed]'
```

Both install into an isolated venv — clean uninstall, no impact on system Python. The `fastembed` extra pulls the bundled embedder (ONNX-based, no service required).

The `grimoire` command is now on your `PATH`.

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

## Quickstart

```sh
export GRIMOIRE_MOUNT=$PWD/.grimoire

# Create the mount + default DB. Idempotent.
grimoire mount create

# Add an entry. `--keyword-text` and `--semantic-text` are independent —
# pick one, both, or neither (metadata-only).
grimoire entry add \
    --group-key creature \
    --group-ref phoenix-001 \
    --payload '{"habitat": "volcano"}' \
    --context "discovered in the southern volcanic chain" \
    --keyword-text "phoenix fire-bird ashes" \
    --semantic-text "A solar phoenix reborn from its own ashes at dawn"

# Search.
grimoire search semantic "creatures that come back from the dead"
grimoire search keyword "phoenix"

# Browse chronologically; each id IS its own cursor.
grimoire fetch --limit 50

# Inspect the database — model, dimension, schema version, counts, file size.
grimoire info
```

Every command prints pretty-indented JSON. Pipe through `jq` for filtering and extraction.

## Commands

### Mount administration

#### `grimoire mount create`

Create the mount directory, shared `__models__/` cache, and the default database. Idempotent — safe to re-run. Loads the default embedder on first create to write the embedder lock.

#### `grimoire mount destroy --yes`

Wipe the entire mount: every database, the model cache, the registry. There is no undo. `--yes` is required.

#### `grimoire mount add <name>`

Create a named database in the mount. Errors if the database already exists. The mount itself must already exist (run `mount create` first).

#### `grimoire mount ls`

List databases in the mount as a JSON array of `{"db": <str|null>, "path": <str>}`. The default DB appears first with `db: null`; named DBs follow alphabetically.

#### `grimoire mount remove <name> --yes`

Delete a single named database file from the mount. The model cache and other databases are preserved. `--yes` is required.

### Database inspection

#### `grimoire info [--db <name>]`

Show metadata for a database: embedder lock (`model`, `dimension`), `schema_version`, `entry_count`, per-group and per-partition counts, file path, file size. Does not load the embedder.

#### `grimoire fetch [filters] [--cursor <id>] [--limit <n>]`

List entries chronologically, with optional filters and ULID-cursor paging.

| Option | Behavior |
|---|---|
| `--db`, `-d` | Target a named DB. Omit for the default. |
| `--id` | Filter to entries with this id. Repeatable. |
| `--group-key` | Filter to entries with this `group_key`. Repeatable. |
| `--group-ref` | Filter to entries with this `group_ref`. Repeatable. |
| `--cursor` | Return entries with `id > <cursor>`. Pass the last id of the previous page. |
| `--limit` | Maximum entries to return (default 100). |

```sh
LAST=$(grimoire fetch --limit 100 | jq -r '.[-1].id')
grimoire fetch --limit 100 --cursor "$LAST"
```

### Entry CRUD

#### `grimoire entry add [options]`

Create an entry. Pass `--keyword-text` and/or `--semantic-text` to (re-)index in the same call; omit both for a metadata-only record.

| Option | Behavior |
|---|---|
| `--db`, `-d` | Target a named DB. |
| `--group-key` | Group key metadata. |
| `--group-ref` | External reference id within the group. |
| `--context` | Unindexed contextual prose (not searched). |
| `--payload` | JSON object string. |
| `--keyword-text` | Text written to the FTS5 row. Triggers a keyword (re-)index. |
| `--threshold-rank` | Minimum BM25 score for keyword hits (non-negative). Requires `--keyword-text`. |
| `--semantic-text` | Text written to the vec row. Triggers an embed (re-)index. |
| `--partition` | Vec partition to write into. Requires `--semantic-text`. |
| `--threshold-distance` | Maximum vector distance for semantic hits (non-negative). Requires `--semantic-text`. |

`(group_key, group_ref)` collisions raise an error.

#### `grimoire entry update <id> [options] [--put]`

Update `group_key`, `group_ref`, `payload`, and `context` on an entry. Default is partial-update: unspecified fields keep their current value. Pass `--put` to switch to replace mode — any field not given on the command line is set to NULL.

The same indexing options as `entry add` are accepted: passing `--keyword-text` always replaces the FTS5 row, and `--semantic-text` always replaces the vec row. Leaving them off preserves the existing index rows. Indexing is decoupled from `--put`.

#### `grimoire entry get <id>`

Fetch a single entry by id.

#### `grimoire entry delete <id> --yes`

Delete an entry, cascading to its FTS and vec rows. `--yes` is required.

### Searching

#### `grimoire search keyword <query> [filters] [--limit <n>]`

FTS5 BM25 search. Free-form prose is tokenized into safe FTS5 syntax automatically: each word token is quoted and joined with `OR`, so apostrophes, punctuation, and bareword FTS5 operators (`AND`, `OR`, `NOT`, `NEAR`, `*`) in the query can't reach the parser.

| Option | Behavior |
|---|---|
| `--db`, `-d` | Target a named DB. |
| `--id` | Restrict hits to these ids. Repeatable. |
| `--group-key` | Restrict hits to these group keys. Repeatable. |
| `--group-ref` | Restrict hits to these group refs. Repeatable. |
| `--limit` | Maximum hits (default 10). |

Returns hits as `{"entry": {...}, "rank": <bm25>}`. `rank` is the BM25 score (higher = better, non-negative); the entry carries its `keyword_text` and `threshold_rank` inline.

#### `grimoire search semantic <query> [--partition <p>] [--limit <n>]`

vec0 KNN search. Embeds the query via the bundled embedder, then ranks by vector distance.

| Option | Behavior |
|---|---|
| `--db`, `-d` | Target a named DB. |
| `--partition` | Restrict KNN to this partition. Omit to span every partition. |
| `--limit` | Maximum hits (default 10). |

Returns hits as `{"entry": {...}, "distance": <float>}`. `distance` is the vec0 distance (lower = better, non-negative); the entry carries its `semantic_text`, `partition`, and `threshold_distance` inline.

### MCP server

#### `grimoire mcp serve`

Run a FastMCP server over stdio, scoped to this mount. Wires the same read+write surface as the CLI into MCP tools that an AI client can call directly. Mount administration (`mount create/destroy/add/remove`) stays CLI-only.

Tools exposed: `info`, `fetch`, `entry_get`, `entry_add`, `entry_update`, `entry_delete`, `search_keyword`, `search_semantic`. `entry_add` and `entry_update` accept the same `keyword_text`/`semantic_text` (plus thresholds and `partition`) params as their CLI counterparts.

## Output format

Every command prints pretty-indented JSON to stdout. There is no table mode, no `--raw`, no auto-detect — JSON in, JSON out, pipe to `jq` to slice. Errors go to stderr via Typer's standard messaging.

## Environment variables

| Variable | Default | Behavior |
|---|---|---|
| `GRIMOIRE_MOUNT` | `~/.grimoire` | Mount directory. Overridden by `--mount`. |

## Schema notes

Pre-v1, schema changes are not migrated in place. The library checks `PRAGMA user_version` against its expected `SCHEMA_VERSION` on every open; mismatches raise `SchemaVersionError`. Recreate the file (and re-index its contents) when this happens. Migration ergonomics get designed once v1 is on the table.

## Uninstall

```sh
uv tool uninstall 4lt7ab-grimoire-cli
# or: pipx uninstall 4lt7ab-grimoire-cli
```

The mount directory at `~/.grimoire` (or wherever you pointed `GRIMOIRE_MOUNT`) is left intact. Remove it manually with `grimoire mount destroy --yes` *before* uninstalling, or `rm -rf` after.
