# Examples

Standalone demos that exercise the `grimoire` library. Each is a single runnable Python file — read top to bottom and you've seen the whole thing.

## Setup

Once, from the repo root:

```sh
uv sync --extra fastembed
```

The first run of either example will download the default embedding model (`BAAI/bge-small-en-v1.5`, ~30MB ONNX) into the fastembed cache.

## Demos

### `riddle_tower.py` — a tower of semantic riddles

Five floors, each guarded by a riddle. The accepted answers for each floor are stored in the grimoire with their own `threshold` — type a guess, and the door opens only if your phrasing is close enough by semantic distance. Quit with `quit` or Ctrl+C; resume any time.

```sh
uv run python examples/riddle_tower.py
```

Showcases `dynamic_threshold` (per-record similarity gates), `kind` partitioning (one floor per kind), and persistence (progress lives in the same grimoire file).

### `bestiary.py` — a creature catalog with a CLI

A tiny domain-shaped CLI in ~90 lines. `add` to record a creature, `find` to query in natural language, `list` to browse, `edit` to revise an entry in place, `remove` to delete. Sits directly on the library — everything else is delegated.

```sh
uv run python examples/bestiary.py add --kind dragon "Old Wyrm of the Ash Peaks. Hoards obsidian, breathes fire."
uv run python examples/bestiary.py add --kind phoenix "Solar phoenix. Reborn at dawn from its own ashes."
uv run python examples/bestiary.py find "creatures that come back from the dead"
uv run python examples/bestiary.py list --kind dragon
uv run python examples/bestiary.py edit <id> --description "Old Wyrm of the Ash Peaks. Hoards obsidian and starlight."
```

Showcases the full single-record surface (`add`, `search`, `list`, `update`, `delete`) used in a way that scales to any catalog problem — swap "creatures" for whatever your domain stores.

## Local files

Each example writes its grimoire and model cache under `examples/.local/`. The whole directory is git-ignored — delete it to start fresh.
