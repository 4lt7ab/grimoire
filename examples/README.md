# Examples

Four runnable Python scripts. The first three put one of grimoire's three sidecars in the spotlight while leaving the others quiet, so the facet under demo is unmistakable. The fourth — `spellweaver` — uses semantic search as a *dispatcher* rather than a search box, and is built as an interactive game.

| Example | Primary facet | One-liner |
|---|---|---|
| [`ai-journal/`](ai-journal/) | semantic memory (`entry_vec`) | Natural-language recall over personal log entries. |
| [`snippet-vault/`](snippet-vault/) | keyword search (`entry_fts`) | FTS5 BM25 search across code snippets. |
| [`reading-list/`](reading-list/) | filterable catalog (`entry_idx`) | Status / priority / date filters plus URL lookup over reading-list items. |
| [`spellweaver/`](spellweaver/) | semantic dispatch (`entry_vec`) | Interactive combat REPL — free-form incantations are matched to canonical spells. |

## Running

From the workspace root:

```sh
uv run examples/ai-journal/app.py
uv run examples/snippet-vault/app.py
uv run examples/reading-list/app.py
uv run examples/spellweaver/app.py            # interactive
uv run examples/spellweaver/app.py --demo     # scripted scenario
```

Each script seeds a small dataset (idempotently) and then runs a handful of demo queries (or, in `spellweaver`'s case, a combat loop). Re-running the script reuses the existing data — drop the example's `.grimoire/` directory to reset.

## Local mount layout

Each example owns its own grimoire mount at `examples/<name>/.grimoire/`. The directory is created on first run and is gitignored, so the database file and (for `ai-journal`) the embedder model cache all stay local to the example.

| Example | Embedder | Notes |
|---|---|---|
| `ai-journal` | `FastembedEmbedder` (`BAAI/bge-small-en-v1.5`) | First run downloads ~30 MB into `examples/ai-journal/.grimoire/__models__/`. |
| `snippet-vault` | `NoOpEmbedder` | No embeddings; no model download. |
| `reading-list` | `NoOpEmbedder` | No embeddings; no model download. |
| `spellweaver` | `FastembedEmbedder` (same model) | Same ~30 MB cache; symlink to `ai-journal`'s `__models__/` to share weights. |

## In a real app

These showcases use one sidecar each to keep the spotlight focused. In production the writer is built to combine sidecars: every `index()` call accepts any subset of `ref` / `ord` / `match` / `search` in the same transaction, so a single entry can be filterable, keyword-searchable, and semantically-searchable at once.
