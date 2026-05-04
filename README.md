# grimoire

A SQLite and sqlite-vec backed semantic search tool. It provides a simple API into an indexed, polymorphic dataset hosted in SQLite, sparing developers the work of wiring up a database and repository layer themselves.

Aimed at developers building applications that need semantic search alongside a simple schema for managing non-relational data.

## Install

Requires [`asdf`](https://asdf-vm.com/). Python, `uv`, and `just` are pinned via `.tool-versions`.

```sh
asdf install
uv sync
```

To use the bundled embedder (`FastembedEmbedder`), install the optional extra:

```sh
uv sync --extra fastembed
```

Without it, you'll need to provide your own implementation of the `Embedder` Protocol.

## Use cases

- Semantic gates — heuristic-driven filtering over indexed data.
- AI application memory.
- Knowledgebase search.
- Quickstart datastore for apps that need search on day one.

## Library

A grimoire is a single SQLite file. `Grimoire.open(path, embedder=...)` is idempotent — it creates the file if missing, opens it if present. The embedding model and its dimension are locked when the file is first created; reopening with a different model raises `GrimoireMismatch`.

```python
from grimoire import Grimoire
from grimoire.embedders import FastembedEmbedder

embedder = FastembedEmbedder(cache_folder="./.grimoire/models")

with Grimoire.open("memory.db", embedder=embedder) as g:
    g.add(kind="note", content="the moon is full tonight")
    g.add(kind="note", content="dragons fly at midnight")

    for entry in g.search("celestial events", k=5):
        print(entry.id, entry.distance, entry.content)
```

The full API: `Grimoire.open`, `add`, `get`, `list`, `search`, `delete`, `close`. `add` and the read methods accept an optional `kind=` filter; `search` also accepts `dynamic_threshold=True` to gate results by each record's stored similarity threshold.

## CLI

```sh
# Bulk-ingest from a JSONL file
grimoire ingest data.jsonl --db memory.db

# Semantic search
grimoire search "celestial events" --db memory.db --k 5

# List entries in chronological order, with cursor pagination
grimoire list --db memory.db --limit 100
grimoire list --db memory.db --limit 100 --after-id <last_id>

# Fetch or delete by id
grimoire get <entry_id> --db memory.db
grimoire delete <entry_id> --db memory.db
```

JSONL format for `ingest`:

```jsonl
{"kind": "note", "content": "the moon is full"}
{"kind": "note", "content": "dragons fly at midnight", "payload": {"src": "diary"}, "threshold": 0.5}
```

The CLI reads the embedding model name from the database file, so `--model` is only consulted when creating a new file.

## Documentation

- [Architecture](docs/architecture.md)
- [Feature set](docs/feature-set.md)
- [Coding conventions](docs/coding-conventions.md)
