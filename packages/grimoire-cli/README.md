# 4lt7ab-grimoire-cli

The standalone CLI for grimoire — a single-file semantic datastore backed by SQLite and [`sqlite-vec`](https://github.com/asg017/sqlite-vec).

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
├── grimoire.toml          # registry of named DBs (lazy)
├── models/                # shared embedder cache
├── grimoire.db            # the default DB
└── <name>/
    └── grimoire.db        # a named DB
```

The mount resolves in this order: `--mount <dir>` flag > `GRIMOIRE_MOUNT` env var > `~/.grimoire`.

Set the env var once per shell to avoid passing `--mount` everywhere:

```sh
export GRIMOIRE_MOUNT=$PWD/.grimoire
```

A mount can hold one **default database** at `<mount>/grimoire.db` (no name) plus any number of **named databases** under `<mount>/<name>/grimoire.db`, tracked in `<mount>/grimoire.toml`. Pick which one a command targets with `--db <name>`; omit `--db` to target the default.

## Quickstart

```sh
export GRIMOIRE_MOUNT=$PWD/.grimoire

# Set up the mount + default database. Idempotent.
grimoire mount

# Add a few entries.
grimoire entry add "the moon is full tonight"
grimoire entry add "dragons fly at midnight" --keyword-text "dragon midnight flight"
grimoire entry add "knights joust at dawn" --group-key tale

# Search.
grimoire search "celestial events"
grimoire search "dragon" --mode keyword

# List chronologically; each id IS its own pagination cursor.
grimoire query --limit 50

# Inspect the file (model, dimension, entry count, per-group counts).
grimoire
```

All read commands print one JSON object per line when piped or with `--raw`; pretty tables at the terminal. Pipe through `jq` for filtering.

## Commands

### Top-level

#### `grimoire` *(no subcommand)*

Print metadata for the default database in the mount — model, dimension, schema version, entry count, per-group counts. Exits non-zero if no database exists at `<mount>/grimoire.db`.

```sh
grimoire
grimoire --mount /path/to/mount
grimoire --raw                 # force JSONL output at TTY
```

#### `grimoire mount [--model M]`

Set up the mount and its default database. Creates the mount directory and shared `models/` cache if missing, creates the default database at `<mount>/grimoire.db` if missing, then prints the same JSONL listing as `grimoire ls`. Idempotent — re-running on a mount whose default database already exists skips the embedder load entirely. `--model` is consulted only on first create; passing it against an existing database whose locked model differs is an error.

#### `grimoire mount destroy [--yes]`

Wipe the entire mount: every database, the manifest, the model cache. There is no undo. Use `grimoire destroy [NAME]` for per-database removal.

#### `grimoire create <name> [--model M] [--description D]`

Create a new named database in the mount. Strict: errors if a database with this name already exists. The mount directory and shared model cache are created on demand if missing — no need to run `grimoire mount` first when you only want named DBs. `--description` is recorded in the manifest.

#### `grimoire ls`

List databases in the mount. Default database (if present) is listed first, then named databases in alphabetical order. Pretty table at the terminal; JSONL when piped or with `--raw` — one object per database (`name` is `null` for the default).

#### `grimoire destroy [name] [--yes]`

Delete a single database from the mount. Without `NAME`, drops the default database at `<mount>/grimoire.db`. With `NAME`, drops `<mount>/<name>/grimoire.db` and removes its manifest entry. Idempotent — missing files or manifest entries are tolerated.

### Reading

#### `grimoire query [filters/paging]`

List entries chronologically with optional filters and ULID-cursor paging.

| Option | Behavior |
|---|---|
| `--db NAME` | Target a named DB. Omit for the default. |
| `--group-key K` | Filter to entries with this `group_key`. |
| `--group-ref R` | Filter to entries with this `group_ref`. |
| `--after ISO` | ISO 8601 lower bound on entry creation time (inclusive). |
| `--before ISO` | ISO 8601 upper bound on entry creation time (exclusive). |
| `--cursor ID` | Return entries with `id > this`. The id of the last entry from the previous page. |
| `--limit N` | Maximum number of entries to return (default 100). |

The id IS the cursor — ULIDs sort lexicographically by creation time, so `id > cursor` walks the next page in chronological order without a separate cursor type:

```sh
LAST=$(grimoire query --limit 100 | tail -1 | jq -r .id)
grimoire query --limit 100 --cursor "$LAST"
```

#### `grimoire search "QUERY" [--mode MODE] [filters]`

Run a vector or keyword search against a database.

| Option | Behavior |
|---|---|
| `--mode {vector,keyword}` | `vector` (default) for semantic similarity; `keyword` for FTS5 BM25. |
| `--db NAME` | Target a named DB. |
| `--group-key K` | Filter to entries with this `group_key`. |
| `--after ISO` | Lower bound on creation time (inclusive). |
| `--before ISO` | Upper bound on creation time (exclusive). |
| `-k`, `--k N` | Number of results to return (default 10). |
| `--dynamic-threshold` | Filter results by each entry's stored similarity threshold. Vector mode only. |

Time filters and `--dynamic-threshold` apply **after** the vector KNN's top-k — narrow windows or tight thresholds can return fewer than `k` results. Raise `-k` to compensate. `search` returns top-`k` by score, so paging doesn't apply.

The `keyword` mode accepts FTS5 syntax — phrases (`"exact phrase"`), prefix (`fire*`), boolean operators (`phoenix OR wyrm NOT egg`).

### Writing entries

#### `grimoire entry add [VECTOR_TEXT] [--keyword-text KT] [options]`

Add a single entry. `VECTOR_TEXT` is an optional positional — pass it for the common case of an entry whose meaning is its prose. Both `vector_text` and `keyword_text` are independent: pass either, both, or neither.

| Option | Behavior |
|---|---|
| *(positional)* | The `vector_text` — embedded for `vector_search`. Omit to skip the vector index. |
| `--keyword-text TEXT` | Free-form text indexed for FTS5 BM25. Omit to skip the keyword index. |
| `--db NAME` | Target a named DB. |
| `--group-key K` | Group label for partitioning. |
| `--group-ref R` | Consumer-set unique reference within the group. Collisions on `(group_key, group_ref)` raise an error. |
| `--payload JSON` | JSON object to attach as the entry payload. |
| `--threshold N` | Per-entry similarity threshold for `--dynamic-threshold` searches. |

```sh
grimoire entry add "the moon is full tonight"
grimoire entry add "dragons fly at midnight" \
    --keyword-text "dragon midnight flight" \
    --group-key tale --group-ref dragon-001 \
    --payload '{"era": "third age"}' \
    --threshold 0.6
grimoire entry add --keyword-text "phoenix fire-bird" --payload '{"id": "p001"}'
```

The last form creates a payload-only entry: no vector_text, no FTS prose, just structured data addressable by `--group-ref` and findable by the `--keyword-text` tokens.

#### `grimoire entry update <entry_id> [--payload JSON | --clear-payload] [--threshold N | --clear-threshold]`

Patch the mutable metadata fields on an entry. **Only `payload` and `threshold` are mutable.** The indexed and identity fields (`vector_text`, `keyword_text`, `group_key`, `group_ref`) are immutable after creation — to change them, delete the entry and add a fresh one.

Omit a field to leave it alone; pass the value to replace it; pass the matching `--clear-*` flag to set it to `NULL`. Value and clear flags are mutually exclusive.

```sh
grimoire entry update 01HXXXXXXXXXXXXXXXXXXXXXXX --payload '{"updated": true}'
grimoire entry update 01HXXXXXXXXXXXXXXXXXXXXXXX --clear-threshold
```

#### `grimoire entry get <entry_id>`

Fetch a single entry by id. Pretty key-value at the terminal; JSON when piped or with `--raw`.

#### `grimoire entry delete <entry_id>`

Delete an entry by id, including its vector and FTS index rows.

### Bulk import / export

#### `grimoire entry import <jsonl-file> [--db NAME]`

Bulk-import records into a database from a JSONL file. Additive: records are appended to the existing database. Collisions on `(group_key, group_ref)` raise an error and abort the import — the file must be free of conflicts with existing records, or the conflicting records must be removed first.

#### `grimoire entry export [-o PATH] [--force] [--db NAME]`

Export every entry in a database to a JSONL file. Defaults to `<mount>/export.jsonl`. Refuses to overwrite an existing file unless `--force` is set.

The output format mirrors `import`'s expected input — entries can be round-tripped (`vector_text`, `keyword_text`, `group_key`, `group_ref`, `payload`, and `threshold` are preserved). Ids are NOT preserved on round-trip; they're grimoire-assigned and re-imported records get fresh ULIDs.

## JSONL format

One object per line. Every field is optional. `vector_text` is the text grimoire embeds for `vector_search`; `keyword_text` is the text grimoire indexes for `keyword_search`; `payload` is the structured object returned alongside a match.

```jsonl
{"group_key": "creature", "group_ref": "phoenix-001", "vector_text": "A solar phoenix reborn from its own ashes", "keyword_text": "phoenix fire-bird ashes", "payload": {"habitat": "volcano"}}
{"group_key": "creature", "group_ref": "wyrm-014", "vector_text": "An ancient wyrm hoarding obsidian in the Ash Peaks", "payload": {"habitat": "mountain"}, "threshold": 0.5}
{"group_key": "creature", "group_ref": "p001", "keyword_text": "phantom wraith specter", "payload": {"id": "p001"}}
```

Three valid shapes shown above: vector + keyword (full record), vector-only (no FTS hit), keyword-only (no embedding). A record with neither `vector_text` nor `keyword_text` is also valid — it's a payload-only entry, addressable by id / `group_ref` / `query`.

## Output conventions

Read commands (`query`, `search`, `entry get`, `ls`, bare `grimoire`) auto-detect output mode:

- **Pretty** at a TTY — Rich tables for collections, key-value blocks for single records.
- **JSONL** when piped — one JSON object per line, suitable for `jq` and downstream processing.
- **`--raw`** — force JSONL at the terminal. Useful for inspecting the raw shape interactively.

Write commands (`entry add`, `entry update`) print the resulting entry in the same auto-detected mode.

## Environment variables

| Variable | Default | Behavior |
|---|---|---|
| `GRIMOIRE_MOUNT` | `~/.grimoire` | Mount directory. Overridden by `--mount`. |

## Schema notes

Pre-v1, schema changes are not migrated in place. The library checks `PRAGMA user_version` against its expected `SCHEMA_VERSION` on every open; mismatches raise `SchemaVersionError`. The intended response is to `entry export` from the old grimoire on the older library version, upgrade, then `entry import` into a freshly created grimoire. Migration ergonomics get designed once v1 is on the table.

## Uninstall

```sh
uv tool uninstall 4lt7ab-grimoire-cli
# or: pipx uninstall 4lt7ab-grimoire-cli
```

The mount directory at `~/.grimoire` (or wherever you pointed `GRIMOIRE_MOUNT`) is left intact. Remove it manually with `grimoire mount destroy --yes` *before* uninstalling, or `rm -rf` after.
