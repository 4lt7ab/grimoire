<!-- BEGIN grimoire-cheatsheet -->
## Grimoire CLI

`grimoire` is a single-file SQLite + sqlite-vec semantic datastore. The CLI manages **mounts** — a directory holding one or more databases plus a shared embedder model cache.

**Mount resolution:** `--mount <dir>` flag > `GRIMOIRE_MOUNT` env var > `~/.grimoire`. Set `GRIMOIRE_MOUNT` once per shell to avoid passing `--mount` everywhere.

**Mount layout:**

```
<mount>/
├── grimoire.toml      # registry of named DBs
├── models/            # shared embedder cache
├── grimoire.db        # the default DB
└── <name>/grimoire.db # a named DB
```

**Top-level commands:**

| Command | Purpose |
|---|---|
| `grimoire` | Print metadata for the default DB (model, dimension, entry count, per-group counts). |
| `grimoire mount` | Create the mount + default DB. Idempotent. |
| `grimoire mount destroy --yes` | Wipe the entire mount (every DB, manifest, model cache). No undo. |
| `grimoire ls` | List databases in the mount. |
| `grimoire create <name> [--description D]` | Create a named DB. |
| `grimoire destroy [name] --yes` | Delete one DB. |

**Reading:**

| Command | Purpose |
|---|---|
| `grimoire query [--group-key K] [--after ISO] [--before ISO] [--cursor ID] [--limit N]` | List entries chronologically. The id IS the cursor — pass the last id from the previous page. |
| `grimoire search "QUERY" [--mode {vector,keyword}] [--group-key K] [-k N] [--dynamic-threshold]` | Vector (default) or FTS5 BM25 keyword search. Keyword mode supports phrases (`"exact phrase"`), prefix (`fire*`), boolean (`a OR b NOT c`). |
| `grimoire entry get <id>` | Fetch a single entry. |

**Writing:**

| Command | Purpose |
|---|---|
| `grimoire entry add [VECTOR_TEXT] [--keyword-text K] [--group-key G] [--group-ref R] [--payload JSON] [--threshold N]` | Add an entry. All fields optional — payload-only entries are valid. |
| `grimoire entry update <id> [--payload JSON \| --clear-payload] [--threshold N \| --clear-threshold]` | Patch mutable fields. Only `payload` and `threshold` are mutable; `vector_text`, `keyword_text`, `group_key`, `group_ref` are immutable after creation. |
| `grimoire entry delete <id>` | Delete an entry. |
| `grimoire entry import <jsonl>` | Bulk-append from JSONL. Aborts on `(group_key, group_ref)` collision. |
| `grimoire entry export [-o PATH] [--force]` | Dump every entry to JSONL. Round-trips through import (ids are reassigned). |

**Per-DB targeting:** every reading / writing command takes `--db <name>` to target a named DB; omit for the default at `<mount>/grimoire.db`.

**Output:** pretty tables at a TTY, JSONL when piped (or `--raw` to force JSONL at TTY). Pipe through `jq` for filtering.

**JSONL fields** (import / export, all optional): `vector_text`, `keyword_text`, `group_key`, `group_ref`, `payload`, `threshold`. A record with neither `vector_text` nor `keyword_text` is a payload-only entry.

**Schema:** pre-v1, schema changes are not migrated in place. Mismatch raises `SchemaVersionError` — the fix is `entry export` from the old grimoire, upgrade, then `entry import` into a fresh DB.

For exhaustive flag listings: `grimoire --help`, `grimoire <subcommand> --help`.
<!-- END grimoire-cheatsheet -->
