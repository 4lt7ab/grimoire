# Reading list

Showcases **`entry_idx`** — filterable / sortable structured metadata.

A reading-list catalog. Each entry stores article metadata in `data` (title, author, url) and indexes the same fields into `entry_idx`: the URL goes into `uniq_ref` (sparse-unique, so each URL maps to at most one entry) and five ordinals carry the structured side — status, priority, added date, estimated word count, source kind. Queries use the `Filters` dataclass for `equals` / `gte` / `lte` against any of those columns.

The other two sidecars stay empty by design — no `entry_fts`, no `entry_vec`. Uses `NoOpEmbedder` so there's no model download.

## What it runs

1. Seeds 12 reading-list items on first run.
2. Runs four `query()` filter combinations and one `fetch()` lookup by URL, printing matching rows in `uniq_id` order.

## Why this shape

A structured-only grimoire is the right shape when:

- Items have a small, fixed set of attributes you want to filter and page by.
- External references (URLs, ticket IDs, ISBNs) drive lookup.
- Free-text search isn't the primary access pattern.

The ordinal columns are BLOB-affinity — they accept any JSON-serializable scalar, so `ordinal_1 = "unread"` (TEXT) and `ordinal_2 = 4` (INT) and `ordinal_3 = "2026-04-15"` (TEXT, ISO date) all coexist on the same row without separate column types. Comparison uses SQLite's storage-class precedence, so callers typically stick to one type per ordinal slot.

Run after a bulk load to refresh planner stats:

```sh
uv run examples/reading-list/app.py    # seeds + queries
# After re-seeding many rows:
grimoire --mount examples/reading-list/.grimoire analyze
```

## Run

```sh
uv run examples/reading-list/app.py
```
