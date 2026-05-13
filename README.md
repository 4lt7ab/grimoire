# grimoire

A single-file semantic datastore. Entries hold metadata; keyword (FTS5) and semantic (vec0) indexing are independent, opt-in operations against the same entry id. Backed by SQLite and [`sqlite-vec`](https://github.com/asg017/sqlite-vec).

Aimed at developers who want search-indexed memory without spinning up a vector database.

## Use cases

- AI application memory.
- Knowledgebase search with a payload alongside each hit.
- Catalog datastores where some records have searchable prose and others are payload-only.
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
from grimoire import grimoire
from grimoire.data.entry import Entry
from grimoire.embed import FastembedEmbedder

with grimoire.open("grimoire.db", embedder=FastembedEmbedder()) as g:
    [entry] = g.add([
        Entry(
            id=None,
            group_key="creature",
            group_ref="phoenix-001",
            payload={"habitat": "volcano"},
            context=None,
        ),
    ])
    g.keyword([(entry.id, "phoenix fire-bird ashes")])
    g.embed([(entry.id, "A solar phoenix reborn from its own ashes at dawn")])

    for hit in g.semantic_search("creatures that come back from the dead"):
        print(hit.distance, hit.semantic_text, hit.entry.payload)
```

CLI:

```sh
export GRIMOIRE_MOUNT=$PWD/.grimoire
grimoire mount create
grimoire entry add \
    --group-key creature --group-ref phoenix-001 \
    --payload '{"habitat": "volcano"}' \
    --keyword-text "phoenix fire-bird ashes" \
    --semantic-text "A solar phoenix reborn from its own ashes at dawn"
grimoire search semantic "creatures that come back from the dead"
```

The shape of an entry is the same on both sides: a metadata row plus optional keyword and semantic indexes. The library exposes them as separate `keyword()` and `embed()` calls; the CLI rolls them into `entry add`/`entry update` for convenience.

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
