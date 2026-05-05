# grimoire

A single-file semantic datastore. Drop in records, query them by meaning. Backed by SQLite and [`sqlite-vec`](https://github.com/asg017/sqlite-vec).

Aimed at developers who want search-indexed memory without spinning up a vector database.

## Use cases

- AI application memory.
- Semantic gates — heuristic-driven filtering over indexed data.
- Knowledgebase search.
- Quickstart datastore for apps that need search on day one.

## Install

### CLI

```sh
uv tool install '4lt7ab-grimoire-cli[fastembed]'
# or: pipx install '4lt7ab-grimoire-cli[fastembed]'
```

Both install into an isolated venv — clean uninstall, no impact on your system Python.

### As a dependency

```sh
uv add '4lt7ab-grimoire[fastembed]'
```

The `fastembed` extra pulls the bundled `FastembedEmbedder` (ONNX-based, no service required). To bring your own embedder instead, drop the extra and implement the `Embedder` Protocol — see [Custom embedders](#custom-embedders).

### From source

Requires [`asdf`](https://asdf-vm.com/). Python, `uv`, and `just` are pinned via `.tool-versions`.

```sh
asdf install
uv sync --extra fastembed
```

## Quickstart

Once installed, the `grimoire` command is on your `PATH`.

Stage a local working directory. Everything below lives under `.grimoire/`, which is already git-ignored.

```sh
mkdir -p .grimoire
export GRIMOIRE_MOUNT=$PWD/.grimoire
```

Confirm there's no grimoire there yet — also doubles as a check that your env vars resolved to the path you expected:

```sh
grimoire info
```

You should see `Error: No grimoire at <full path>` (exit 1).

Initialize the datastore. This creates `<mount>/grimoire.db`, writes the embedder lock, and downloads the default embedding model (`BAAI/bge-small-en-v1.5`, ~30MB ONNX) into `<mount>/models/` — one deliberate setup step.

```sh
grimoire init
```

Add a few entries. No download this time — `init` already warmed the model.

```sh
grimoire add "the moon is full tonight"
grimoire add "dragons fly at midnight"
grimoire add "potions bubble in the cauldron"
grimoire add "knights joust at dawn" --kind tale
```

Inspect the file — model, dimension, entry count, kinds:

```sh
grimoire info
```

Search by meaning:

```sh
grimoire search "celestial events"
grimoire search "stories of valor" --kind tale
```

Bulk-load from JSONL. Each row carries a `payload` — the structured object the description is pointing at:

```sh
cat > .grimoire/data.jsonl <<'EOF'
{"kind": "spell", "content": "Summons a sphere of silver light that wards undead and warms cold hands", "payload": {"id": "lumara", "school": "abjuration", "tier": 2}}
{"kind": "spell", "content": "Coaxes a locked door, chest, or cage to forget its keeper", "payload": {"id": "skeleton-key", "school": "transmutation", "tier": 1}}
{"kind": "spell", "content": "Wraps the caster in a curtain of silence so footfalls and whispers vanish", "payload": {"id": "hush", "school": "illusion", "tier": 1}}
EOF
grimoire ingest .grimoire/data.jsonl
```

Search the spells. Notice the `payload` in the result — that's the object you actually wanted to find:

```sh
grimoire search "magic to slip past a guard unseen" --kind spell
```

`hush` should rank first, with its payload (`{"id": "hush", "school": "illusion", "tier": 1}`) right there in the JSON.

List entries chronologically (results are JSON, one per line):

```sh
grimoire list
```

Pipe any read command through `jq` for pretty output:

```sh
grimoire list | jq
```

Reset everything:

```sh
rm -rf .grimoire
```

Uninstall:

```sh
uv tool uninstall 4lt7ab-grimoire-cli
# or: pipx uninstall 4lt7ab-grimoire-cli
```

## Library

```sh
uv add '4lt7ab-grimoire[fastembed]'
```

A grimoire is a single SQLite file. `Grimoire.init(path, embedder=...)` is the one-time setup ritual: it creates the file if missing, writes the embedder lock, validates against any existing lock, and exercises the embedder once so deferred work (model download, weight load) happens at a known moment. After that, `Grimoire.open(path, embedder=...)` opens the existing file cheaply; missing or non-grimoire paths raise `GrimoireNotFound`.

A record has two parts. `content` is the text grimoire embeds and searches against — the description of the thing. `payload` is the optional structured object that description resolves to — the thing itself, the object you actually wanted to find with the query. You search by meaning and get back what the meaning was pointing at.

```python
from grimoire import Grimoire
from grimoire.embedders import FastembedEmbedder

embedder = FastembedEmbedder(cache_folder=".grimoire/models")

with Grimoire.init(".grimoire/memory.db", embedder=embedder) as g:
    g.add(
        kind="creature",
        content="A solar phoenix reborn from its own ashes at dawn",
        payload={"id": "phoenix-001", "habitat": "volcano"},
    )
    g.add(
        kind="creature",
        content="An ancient wyrm hoarding obsidian in the Ash Peaks",
        payload={"id": "wyrm-014", "habitat": "mountain"},
        threshold=0.5,
    )

    for entry in g.search("creatures that come back from the dead", k=5):
        print(entry.id, entry.distance, entry.content, entry.payload)

    volcano_dwellers = g.search(
        "fiery beasts of the magma", kind="creature", dynamic_threshold=True
    )
    everything = g.list(limit=100)
    one = g.get(volcano_dwellers[0].id) if volcano_dwellers else None
    g.delete(everything[0].id)
```

Inspect a file without instantiating an embedder:

```python
from grimoire import Grimoire

stats = Grimoire.peek(".grimoire/memory.db")
if stats:
    print(stats.model, stats.dimension, stats.entry_count, stats.kinds)
```

### Full API

- `Grimoire.init(path, *, embedder)` — create or open the file, validate the embedder lock, exercise the embedder once. The deliberate setup step.
- `Grimoire.open(path, *, embedder)` — open an existing grimoire. Raises `GrimoireNotFound` if the file is missing or not a grimoire.
- `Grimoire.peek(path)` — return `Stats` (or `None` if missing/non-grimoire) without loading the embedder.
- `add(*, kind, content, payload=None, threshold=None)` — insert.
- `get(entry_id)` — fetch by id, or `None`.
- `list(*, kind=None, limit=100, after_id=None)` — chronological pagination.
- `search(query, *, kind=None, k=10, dynamic_threshold=False)` — vector search; `dynamic_threshold` filters results by each record's stored similarity gate.
- `delete(entry_id)` — returns `True` if removed, `False` if absent.
- `close()` — also handled by the context manager.

Errors derive from `GrimoireError`: `GrimoireMismatch` (embedder doesn't match what the file was created with), `GrimoireNotFound` (raised by `open` when the path doesn't point to an existing grimoire), `SchemaVersionError`, `InvalidEmbedder`.

### Custom embedders

`Embedder` is a `Protocol`. Implement three things:

```python
class MyEmbedder:
    @property
    def model(self) -> str: ...
    @property
    def dimension(self) -> int: ...
    def embed(self, text: str) -> list[float]: ...
```

The `model` and `dimension` are written into the file on first open and locked. Reopening with a different model or dimension raises `GrimoireMismatch`.

## CLI reference

If you've installed the CLI, `grimoire --help` prints this same orientation in the terminal — commands, the mount model, output conventions, and environment variables in one screen. The reference below mirrors it for repo browsers.

Every command operates over a grimoire mount: a directory that holds the SQLite file (`<mount>/grimoire.db`) and the embedder model cache (`<mount>/models/`). `info` is the one command that doesn't load the embedder — it inspects the file via `Grimoire.peek` — but it still resolves the db path through the mount.

Pass it explicitly with `--mount <dir>`, or set the environment variable once for the shell:

```sh
export GRIMOIRE_MOUNT=$PWD/.grimoire
```

The flag overrides the env var: `--mount <dir>` over `GRIMOIRE_MOUNT`.

### Commands

```sh
grimoire init
grimoire add "<content>" [--kind K] [--payload '{...}'] [--threshold N]
grimoire ingest <jsonl-file>
grimoire search "<query>" [--k N] [--kind K] [--dynamic-threshold]
grimoire list [--kind K] [--limit N] [--after-id ID]
grimoire get <entry_id>
grimoire delete <entry_id>
grimoire info
```

All read commands print one JSON object per line — pipe to `jq` for filtering.

### JSONL format for `ingest`

One object per line. `kind` and `content` are required; `payload` and `threshold` are optional. `content` is the text that gets embedded and searched; `payload` is the structured object returned alongside a match — the thing the content describes.

```jsonl
{"kind": "creature", "content": "A solar phoenix reborn from its own ashes", "payload": {"id": "phoenix-001", "habitat": "volcano"}}
{"kind": "creature", "content": "An ancient wyrm hoarding obsidian in the Ash Peaks", "payload": {"id": "wyrm-014", "habitat": "mountain"}, "threshold": 0.5}
```

## How it works

- **One file, one model.** The embedder's `model` name and `dimension` are written into the grimoire when it's first created. Reopening with a different embedder raises `GrimoireMismatch` rather than silently producing nonsense vectors. Use `grimoire info` (or `Grimoire.peek` from Python) to see what a file is bound to.
- **Mount is self-contained.** Each mount carries its own `grimoire.db` and its own `models/` cache. The library API is unchanged — it still takes a path to the SQLite file directly; the mount is a CLI convention.
- **No default mount.** The CLI does not invent a `--mount` location for you. Setting `GRIMOIRE_MOUNT` to a `$PWD`-anchored path is the recommended way to keep it stable across `cd`.

## Documentation

- [Architecture](docs/architecture.md)
- [Feature set](docs/feature-set.md)
- [Coding conventions](docs/coding-conventions.md)
