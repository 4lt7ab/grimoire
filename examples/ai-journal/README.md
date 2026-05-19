# AI journal

Showcases **`entry_vec`** — vec0 semantic search.

A journal where every entry is a short narrative plus a tiny structured payload (`date`, `mood`, `text`). The narrative goes into the vec sidecar so later you can ask *"what was I working on related to databases?"* and find entries that never mention "database" verbatim.

The other two sidecars stay empty by design — no `entry_idx`, no `entry_fts`. This is the smallest viable shape for a semantic-only memory.

## What it runs

1. Seeds 8 journal entries (Apr 1–15, varied moods and topics) on first run.
2. Runs three natural-language recall queries and prints the top hits ranked by vec0 distance.

## Run

```sh
uv run examples/ai-journal/app.py
```

First run downloads the default embedder (~30 MB) into `.grimoire/__models__/`. Subsequent runs reuse the cache.

## Why this shape

A semantic-only grimoire opts into the cost of the embedder (`FastembedEmbedder`) and skips the bookkeeping of structured columns. It's the right shape when:

- The query language is natural prose, not literal tokens.
- The corpus is small-to-medium and BM25 keyword search isn't needed alongside.
- Recall by meaning matters more than precise filtering.

If you needed to filter by date or mood as well, you'd add `index(..., ord=(date, mood, None, None, None))` alongside the `search=` kwarg in the same call — no extra round-trip.
