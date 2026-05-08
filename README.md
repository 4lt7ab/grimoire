# grimoire

A single-file semantic datastore. Drop in records, query them by meaning or keyword. Backed by SQLite and [`sqlite-vec`](https://github.com/asg017/sqlite-vec).

Aimed at developers who want search-indexed memory without spinning up a vector database.

## Use cases

- AI application memory.
- Semantic gates — heuristic-driven filtering over indexed data.
- Knowledgebase search.
- Quickstart datastore for apps that need search on day one.

## Two packages

| Package | Use when |
|---|---|
| [**`4lt7ab-grimoire`**](packages/grimoire/README.md) — the Python library | You're embedding grimoire into an application. |
| [**`4lt7ab-grimoire-cli`**](packages/grimoire-cli/README.md) — the CLI | You want a command-line datastore for shells, scripts, ad-hoc work, or `jq` pipelines. |

Both share the same single-file, single-embedder model. A grimoire created by one is fully usable from the other — the CLI is a thin wrapper around the library, plus a mount convention for keeping the SQLite file and the embedder model cache colocated.

## A taste

Library:

```python
from grimoire import Grimoire
from grimoire.embedders import FastembedEmbedder

with Grimoire.create(embedder=FastembedEmbedder(cache_folder=".grimoire/models"), mount=".grimoire") as g:
    g.add(
        group_key="creature",
        vector_text="A solar phoenix reborn from its own ashes at dawn",
        keyword_text="phoenix fire-bird",
        payload={"habitat": "volcano"},
    )
    for hit in g.vector_search("creatures that come back from the dead", k=5):
        print(hit.distance, hit.vector_text, hit.payload)
```

CLI:

```sh
export GRIMOIRE_MOUNT=$PWD/.grimoire
grimoire mount
grimoire entry add "A solar phoenix reborn from its own ashes at dawn" \
    --keyword-text "phoenix fire-bird" \
    --group-key creature \
    --payload '{"habitat": "volcano"}'
grimoire search "creatures that come back from the dead"
```

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

- [Library reference](packages/grimoire/README.md) — full Python API, mount handle, embedder protocol, errors.
- [CLI reference](packages/grimoire-cli/README.md) — every command, every flag, JSONL format, output conventions.
- [Architecture](docs/architecture.md)
- [Feature set](docs/feature-set.md)
- [Coding conventions](docs/coding-conventions.md)
