# grimoire

A single-file semantic datastore. Entries are bare `(uniq_id, data)` rows; three opt-in sidecars (`entry_idx`, `entry_fts`, `entry_vec`) attach typed filterable metadata, keyword search, and semantic search to the same id. Backed by SQLite and [`sqlite-vec`](https://github.com/asg017/sqlite-vec).

Aimed at developers who want search-indexed memory without spinning up a vector database.

## Use cases

- AI application memory.
- Knowledgebase search with structured data alongside each hit.
- Catalog datastores where some records have searchable prose and others are data-only.
- Quickstart datastore for apps that need search on day one.

## Two packages

| Package | Use when |
|---|---|
| [**`4lt7ab-grimoire`**](packages/grimoire/README.md) — the Python library | You're embedding grimoire into an application. |
| [**`4lt7ab-grimoire-cli`**](packages/grimoire-cli/README.md) — the CLI + MCP server | You want a command-line datastore for shells, scripts, `jq` pipelines, or AI-client integration. |

Both share the same single-file, single-embedder model. A grimoire created by one is fully usable from the other.

## A taste

Library:

```python
from grimoire.grimoire import Grimoire
from grimoire.data.entry import Entry
from grimoire.embed import FastembedEmbedder

with Grimoire.open("grimoire.db", embedder=FastembedEmbedder()) as g:
    [entry] = g.add([Entry(uniq_id=None, data={"habitat": "volcano"})])
    g.index(
        entry.uniq_id,
        ref="phoenix-001",
        nom=("creature", None),
        match="phoenix fire-bird ashes",
        search="A solar phoenix reborn from its own ashes at dawn",
    )

    entries, hits = g.search("creatures that come back from the dead")
    for e, h in zip(entries, hits, strict=True):
        print(h.distance, e.data)
```

CLI:

```sh
export GRIMOIRE_MOUNT=$PWD/.grimoire
grimoire mount create
grimoire entry add \
    --data '{"habitat": "volcano"}' \
    --ref phoenix-001 \
    --nom-1 creature \
    --match "phoenix fire-bird ashes" \
    --search "A solar phoenix reborn from its own ashes at dawn"
grimoire search "creatures that come back from the dead"
```

The library exposes `add()` for the bare row and `index()` for the sidecars; the CLI rolls them into a single `entry add` for convenience. Reads (`query`, `fetch`, `match`, `search`) return parallel `(entries, indexes|hits)` tuples — the entry's `data` rides along with every hit, no follow-up call needed.

## Install

```sh
# CLI
uv tool install '4lt7ab-grimoire-cli[fastembed]'
# or: pipx install '4lt7ab-grimoire-cli[fastembed]'

# Library
uv add '4lt7ab-grimoire[fastembed]'
```

The `fastembed` extra pulls the bundled `FastembedEmbedder` (ONNX-based, no service required). Drop the extra and implement the `Embedder` protocol to bring your own — see the [library README](packages/grimoire/README.md#custom-embedders).

## Documentation

- [Library reference](packages/grimoire/README.md) — full Python API, embedder protocol, mount type, errors.
- [CLI reference](packages/grimoire-cli/README.md) — every command, every flag, MCP server.
- [Architecture](docs/architecture.md)
- [Feature set](docs/feature-set.md)
- [Coding conventions](docs/coding-conventions.md)
- [Glossary](docs/glossary.md)
