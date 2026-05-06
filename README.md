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

Attach explicit keywords with repeatable `--keyword` — useful for terms a query might use that don't appear in the prose:

```sh
grimoire add "the silver crown rests on velvet" --kind tale --keyword regalia --keyword treasure
```

Inspect the file — model, dimension, entry count, kinds:

```sh
grimoire info
```

Search by meaning (vector):

```sh
grimoire vector-search "celestial events"
grimoire vector-search "stories of valor" --kind tale
```

Search by literal text (keyword, FTS5):

```sh
grimoire keyword-search "moon"
grimoire keyword-search "knights" --kind tale
```

Or by a keyword attached above — `treasure` isn't in any prose, but the silver crown was tagged with it:

```sh
grimoire keyword-search "treasure" --kind tale
```

Bulk-load from JSONL. Each row carries a `payload` — the structured object the description is pointing at:

```sh
cat > .grimoire/data.jsonl <<'EOF'
{"kind": "spell", "content": "Summons a sphere of silver light that wards undead and warms cold hands", "payload": {"id": "lumara", "school": "abjuration", "tier": 2}, "keywords": ["lumara", "light"]}
{"kind": "spell", "content": "Coaxes a locked door, chest, or cage to forget its keeper", "payload": {"id": "skeleton-key", "school": "transmutation", "tier": 1}, "keywords": ["skeleton-key", "lockpick"]}
{"kind": "spell", "content": "Wraps the caster in a curtain of silence so footfalls and whispers vanish", "payload": {"id": "hush", "school": "illusion", "tier": 1}, "keywords": ["hush", "stealth"]}
EOF
grimoire ingest .grimoire/data.jsonl
```

Search the spells. Notice the `payload` in the result — that's the object you actually wanted to find:

```sh
grimoire vector-search "magic to slip past a guard unseen" --kind spell
```

`hush` should rank first, with its payload (`{"id": "hush", "school": "illusion", "tier": 1}`) right there in the JSON.

Or find spells whose description literally mentions "door":

```sh
grimoire keyword-search "door" --kind spell
```

Or by an explicit keyword that doesn't appear in the prose — `lockpick` is in the spell's `keywords` list, not its description:

```sh
grimoire keyword-search "lockpick" --kind spell
```

Patch an existing entry. Pass only the fields you want to change — everything else is left alone:

```sh
ID=$(grimoire list --kind tale --limit 1 | jq -r .id)
grimoire update "$ID" --content "knights joust at dawn beneath silver banners"
```

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

Records can also carry `keywords` — explicit search terms (aliases, IDs, alternate names) that boost recall in `keyword_search` beyond what the prose alone would match.

Records are mutable: `update` patches a single entry in place (omitted fields are left alone; explicit `None` clears nullable fields), and the bulk variants `add_many`, `update_many`, and `delete_many` apply a list of records in one atomic transaction with a single batched embed call.

```python
from grimoire import Grimoire
from grimoire.embedders import FastembedEmbedder

embedder = FastembedEmbedder(cache_folder=".grimoire/models")

with Grimoire.init(".grimoire/memory.db", embedder=embedder) as g:
    g.add(
        kind="creature",
        content="A solar phoenix reborn from its own ashes at dawn",
        payload={"id": "phoenix-001", "habitat": "volcano"},
        keywords=["phoenix", "fire-bird"],
    )
    g.add(
        kind="creature",
        content="An ancient wyrm hoarding obsidian in the Ash Peaks",
        payload={"id": "wyrm-014", "habitat": "mountain"},
        threshold=0.5,
    )

    for entry in g.vector_search("creatures that come back from the dead", k=5):
        print(entry.id, entry.distance, entry.content, entry.payload)

    for entry in g.keyword_search("phoenix", k=5):
        print(entry.id, entry.rank, entry.content, entry.payload)

    volcano_dwellers = g.vector_search(
        "fiery beasts of the magma", kind="creature", dynamic_threshold=True
    )
    everything = g.list(limit=100)
    one = g.get(volcano_dwellers[0].id) if volcano_dwellers else None

    # Patch a single record. Omitted fields stay as they are.
    g.update(everything[0].id, content="A solar phoenix, reborn anew at every dawn")

    # Bulk patch + bulk delete in one atomic transaction each.
    g.update_many([
        {"id": everything[1].id, "payload": {"id": "wyrm-014", "habitat": "caldera"}},
    ])
    g.delete_many([e.id for e in everything[2:]])
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
- `add(*, kind, content, payload=None, threshold=None, keywords=None)` — insert. `keywords` is an optional list of explicit search terms; they're indexed in FTS5 alongside `content` and weighted 5× higher in `keyword_search` ranking.
- `add_many(records)` — insert many in one transaction with one batched embed call. Each record is a mapping with `kind` and `content` required, plus optional `payload`, `threshold`, `keywords`. Atomic on failure.
- `get(entry_id)` — fetch by id, or `None`.
- `list(*, kind=None, limit=100, after_id=None)` — chronological pagination.
- `vector_search(query, *, kind=None, k=10, dynamic_threshold=False, created_after=None, created_before=None)` — vector search ranked by embedder distance; `dynamic_threshold` filters results by each record's stored similarity gate.
- `keyword_search(query, *, kind=None, k=10, created_after=None, created_before=None)` — keyword search via SQLite FTS5, ranked by BM25 against `content` and `keywords` (keywords weighted 5× higher). The query string accepts FTS5 syntax (phrases, prefix, boolean operators, column scoping like `keywords:phoenix`).
- `update(entry_id, *, kind=None, content=None, payload=..., threshold=..., keywords=...)` — partial patch. Omitted fields are left alone; passing `None` to a nullable field (`payload`, `threshold`, `keywords`) clears it. Re-embeds only when `content` changed; rewrites the vector row only when `content` or `kind` changed; re-indexes FTS only when `content` or `keywords` changed. Returns the updated entry, or `None` if the id is unknown.
- `update_many(records)` — patch many in one transaction with one batched embed call covering only records whose `content` actually changed. Each record must include `id`. Atomic on failure. Duplicate ids in input raise `ValueError`. Returns one `Entry | None` per input record in input order.
- `delete(entry_id)` — returns `True` if removed, `False` if absent.
- `delete_many(ids)` — delete many in one transaction. Duplicate ids each receive the same answer (their pre-call existence). Returns one `bool` per input id in input order.
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
grimoire add "<content>" [--kind K] [--payload '{...}'] [--threshold N] [--keyword K ...]
grimoire ingest <jsonl-file>
grimoire update <entry_id> [--kind K] [--content "..."] [--payload '{...}'] [--threshold N] [--keyword K ...] [--clear-payload | --clear-threshold | --clear-keywords]
grimoire update-many <jsonl-file>
grimoire vector-search "<query>" [--k N] [--kind K] [--dynamic-threshold]
grimoire keyword-search "<query>" [--k N] [--kind K]
grimoire list [--kind K] [--limit N] [--after-id ID]
grimoire get <entry_id>
grimoire delete <entry_id>
grimoire delete-many <ids-file | ->
grimoire info
```

All read commands print one JSON object per line — pipe to `jq` for filtering.

### JSONL format for `ingest`

One object per line. `kind` and `content` are required; `payload`, `threshold`, and `keywords` are optional. `content` is the text that gets embedded and searched; `payload` is the structured object returned alongside a match — the thing the content describes; `keywords` is a list of explicit search terms that augment recall in `keyword_search`.

```jsonl
{"kind": "creature", "content": "A solar phoenix reborn from its own ashes", "payload": {"id": "phoenix-001", "habitat": "volcano"}, "keywords": ["phoenix", "fire-bird"]}
{"kind": "creature", "content": "An ancient wyrm hoarding obsidian in the Ash Peaks", "payload": {"id": "wyrm-014", "habitat": "mountain"}, "threshold": 0.5, "keywords": ["wyrm", "dragon"]}
```

### JSONL format for `update-many`

Same shape, plus a required `id` and PATCH semantics: any field you include is changed, any field you omit is left alone, and an explicit `null` clears nullable fields (`payload`, `threshold`, `keywords`).

```jsonl
{"id": "01J9...A1", "content": "An ancient wyrm hoarding obsidian and starlight in the Ash Peaks"}
{"id": "01J9...B2", "kind": "legend", "keywords": ["phoenix", "fire-bird", "rebirth"]}
{"id": "01J9...C3", "payload": null}
```

Unknown ids are reported in the summary line; they don't fail the run.

### Id-list format for `delete-many`

One ULID per line. Blank lines and `#`-prefixed comments are skipped. Pass `-` to read ids from stdin so the command composes with the read commands:

```sh
grimoire list --kind stale | jq -r .id | grimoire delete-many -
```

## How it works

- **One file, one model.** The embedder's `model` name and `dimension` are written into the grimoire when it's first created. Reopening with a different embedder raises `GrimoireMismatch` rather than silently producing nonsense vectors. Use `grimoire info` (or `Grimoire.peek` from Python) to see what a file is bound to.
- **Mount is self-contained.** Each mount carries its own `grimoire.db` and its own `models/` cache. The library API is unchanged — it still takes a path to the SQLite file directly; the mount is a CLI convention.
- **No default mount.** The CLI does not invent a `--mount` location for you. Setting `GRIMOIRE_MOUNT` to a `$PWD`-anchored path is the recommended way to keep it stable across `cd`.

## Documentation

- [Architecture](docs/architecture.md)
- [Feature set](docs/feature-set.md)
- [Coding conventions](docs/coding-conventions.md)
