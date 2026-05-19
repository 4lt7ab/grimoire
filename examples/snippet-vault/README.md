# Snippet vault

Showcases **`entry_fts`** — FTS5 BM25 keyword search.

A small library of code snippets across languages. Each entry stores `{title, language, description, body}` in `data` and indexes `title + description + body` into the FTS sidecar. Queries pass straight to FTS5, so all of its operators are on the table: phrases (`"on conflict"`), prefix (`rebase*`), boolean (`python AND retry`).

The other two sidecars stay empty by design — no `entry_idx`, no `entry_vec`. Uses `NoOpEmbedder` so there's no model download.

## What it runs

1. Seeds 10 snippets on first run.
2. Runs five FTS5 queries (single token, phrase, prefix, boolean) and prints the top hits ranked by BM25.

## Run

```sh
uv run examples/snippet-vault/app.py
```

## Why this shape

A keyword-only grimoire is the right shape when:

- The corpus has natural language or code where the exact terms matter.
- You need FTS5's expressive operators (phrases, prefix, boolean).
- Semantic recall isn't worth a model dependency.

If you also wanted to filter by language, you'd add `index(..., match=text, ord=(language, None, None, None, None))` — the same call writes both sidecars.
