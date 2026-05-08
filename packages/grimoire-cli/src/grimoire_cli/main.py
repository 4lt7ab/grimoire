import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Annotated, NoReturn

import typer
from grimoire import (
    Entry,
    Grimoire,
    GrimoireError,
    GrimoireNotFound,
    InvalidMount,
)
from grimoire.core import _open_file
from grimoire.mount import (
    MODELS_DIRNAME,
    _db_path,
    _resolve_mount,
)

DEFAULT_MODEL = "BAAI/bge-small-en-v1.5"
EXPORT_FILENAME = "export.jsonl"
# Each batch is one atomic transaction; on failure, only the in-flight batch
# rolls back. Smaller = better recovery granularity, slightly more overhead.
# 200 captures ~95% of fastembed's batching speedup vs single calls.
IMPORT_BATCH_SIZE = 200
PROGRESS_EVERY = 1000

RECOGNIZED_FIELDS = {
    "group_key",
    "group_ref",
    "content",
    "payload",
    "threshold",
    "keywords",
}
REQUIRED_FIELDS = {"content"}


# Reusable annotations — every command shares the mount lookup, and read commands
# share filters/paging. Defining them once keeps help text in lockstep.
Mount = Annotated[
    Path | None,
    typer.Option(
        "--mount",
        help=(
            "Path to the grimoire mount directory. Defaults to ~/.grimoire; "
            "can also be set via the GRIMOIRE_MOUNT environment variable."
        ),
    ),
]
Db = Annotated[
    str | None,
    typer.Option(
        "--db",
        help=(
            "Name of a database within the mount. Omit to target the default "
            "database at <mount>/grimoire.db."
        ),
    ),
]
GroupKey = Annotated[
    str | None,
    typer.Option("--group-key", help="Filter to entries with this group_key."),
]
GroupRef = Annotated[
    str | None,
    typer.Option("--group-ref", help="Filter to entries with this group_ref."),
]
After = Annotated[
    str | None,
    typer.Option(
        "--after",
        help="ISO 8601 lower bound on entry creation time (inclusive).",
    ),
]
Before = Annotated[
    str | None,
    typer.Option(
        "--before",
        help="ISO 8601 upper bound on entry creation time (exclusive).",
    ),
]


app = typer.Typer(
    name="grimoire",
    no_args_is_help=False,
    pretty_exceptions_enable=False,
    epilog=(
        "Environment variables:\n\n"
        "  GRIMOIRE_MOUNT  Default mount directory. Overridden by --mount."
    ),
)
mount_app = typer.Typer(
    name="mount",
    invoke_without_command=True,
    no_args_is_help=False,
    pretty_exceptions_enable=False,
    help=(
        "Set up the mount and its default database, or operate on the mount "
        "itself (e.g. `grimoire mount destroy`)."
    ),
)
app.add_typer(mount_app)


@app.callback(invoke_without_command=True)
def _callback(ctx: typer.Context, mount: Mount = None) -> None:
    """Manage a grimoire datastore — a single-file SQLite + sqlite-vec semantic store.

    With no subcommand, prints metadata for the default database in the mount
    (model, dimension, schema version, entry count, per-group counts).

    Every command operates over a mount directory that holds the SQLite files
    and the shared embedder model cache. A mount can hold one default database
    at <mount>/grimoire.db plus any number of named databases under
    <mount>/<name>/grimoire.db, tracked in <mount>/grimoire.toml. Specify the
    mount with --mount <dir> or set GRIMOIRE_MOUNT; defaults to ~/.grimoire.
    Pick a database within the mount with --db <name>.

    Read commands (query, search, get) print one JSON object per line —
    pipe to `jq` for filtering. Run `grimoire <command> --help` for any
    subcommand.
    """
    resolved = _resolve_mount(mount)
    ctx.obj = {"mount": resolved}
    if ctx.invoked_subcommand is None:
        _emit_info(_db_path(resolved, None), label="default database")


@mount_app.callback(invoke_without_command=True)
def _mount_callback(
    ctx: typer.Context,
    model: Annotated[
        str | None,
        typer.Option(
            help=(
                "fastembed model name. Used only when creating the default "
                "database; passing this against an existing database whose "
                "locked model differs is an error."
            ),
        ),
    ] = None,
) -> None:
    """Set up the mount with its default database.

    With no subcommand, ensures the mount directory and shared model cache
    exist, then creates the default database at <mount>/grimoire.db (or
    validates the existing one). Idempotent — safe to re-run.

    Subcommands operate on the mount as a whole. `grimoire mount destroy`
    wipes the entire mount.
    """
    if ctx.invoked_subcommand is not None:
        return

    mount: Path = ctx.obj["mount"]
    db_file = _db_path(mount, None)

    stats = Grimoire.peek(db_file)
    if stats is not None and model is not None and model != stats.model:
        _fail(
            f"file is locked to model {stats.model!r}; "
            f"drop --model or use a different --mount"
        )

    model_name = stats.model if stats else (model or DEFAULT_MODEL)

    try:
        from grimoire.embedders import FastembedEmbedder

        embedder = FastembedEmbedder(model_name, cache_folder=mount / MODELS_DIRNAME)
    except ImportError as exc:
        _fail(str(exc))

    try:
        if stats is None:
            Grimoire.create(embedder=embedder, mount=mount).close()
        else:
            # Idempotent re-mount: validate the embedder against the existing
            # file via the file-level helper. `Grimoire.open` would auto-load
            # a second fastembed instance, which we already have in hand.
            _open_file(db_file, embedder=embedder).close()
    except GrimoireError as exc:
        _fail(str(exc))

    _emit_info(db_file, label="default database")


@app.command()
def create(
    ctx: typer.Context,
    name: Annotated[
        str,
        typer.Argument(
            help="Name of the new database. Lives at <mount>/<name>/grimoire.db."
        ),
    ],
    model: Annotated[
        str | None,
        typer.Option(
            help=(
                "fastembed model name to lock the new database to. "
                f"Defaults to {DEFAULT_MODEL!r}."
            ),
        ),
    ] = None,
    description: Annotated[
        str | None,
        typer.Option(
            "--description",
            help="Optional human-readable description recorded in the manifest.",
        ),
    ] = None,
) -> None:
    """Create a new named database in the mount.

    Strict: errors if a database with this name already exists. The mount
    directory and shared model cache are created on demand if missing — you
    don't need to run `grimoire mount` first when you only want named DBs.
    """
    mount: Path = ctx.obj["mount"]
    db_file = _db_path(mount, name)
    if db_file.exists():
        _fail(f"database {name!r} already exists at {db_file}")

    model_name = model or DEFAULT_MODEL
    try:
        from grimoire.embedders import FastembedEmbedder

        embedder = FastembedEmbedder(model_name, cache_folder=mount / MODELS_DIRNAME)
    except ImportError as exc:
        _fail(str(exc))

    try:
        Grimoire.create(
            name,
            embedder=embedder,
            mount=mount,
            description=description,
        ).close()
    except GrimoireError as exc:
        _fail(str(exc))
    except InvalidMount as exc:
        _fail(str(exc))

    _emit_info(db_file, label=f"database {name!r}")


@app.command()
def ls(ctx: typer.Context) -> None:
    """List databases in the mount as JSONL — one object per database.

    Default database (if present) is listed first, then named databases in
    alphabetical order. Each line includes name (null for default), model,
    dimension, entry_count, and is_default.
    """
    mount: Path = ctx.obj["mount"]
    handle = Grimoire.mount(mount)
    for info in handle.list():
        typer.echo(
            json.dumps(
                {
                    "name": info.name,
                    "path": str(info.path),
                    "model": info.model,
                    "dimension": info.dimension,
                    "entry_count": info.entry_count,
                    "is_default": info.is_default,
                }
            )
        )


@app.command()
def query(
    ctx: typer.Context,
    db: Db = None,
    group_key: GroupKey = None,
    group_ref: GroupRef = None,
    after: After = None,
    before: Before = None,
    cursor: Annotated[
        str | None,
        typer.Option(
            help=(
                "Pagination cursor: return entries with id > this value. "
                "Pass the id of the last entry from the previous page."
            ),
        ),
    ] = None,
    limit: Annotated[
        int, typer.Option(help="Maximum number of entries to return.")
    ] = 100,
) -> None:
    """List entries chronologically with optional filters and ULID-cursor paging.

    Filters and paging compose. The natural pipeline:

        LAST=$(grimoire query --limit 100 | tail -1 | jq -r .id)
        grimoire query --limit 100 --cursor "$LAST"

    is idiomatic because the entry id IS the cursor — ULIDs sort
    lexicographically by creation time, so `id > cursor` walks the next
    page in chronological order without a separate cursor type.
    """
    mount: Path = ctx.obj["mount"]
    after_dt = _parse_iso("--after", after)
    before_dt = _parse_iso("--before", before)
    with _open_grimoire(mount, db) as g:
        for entry in g.list(
            group_key=group_key,
            group_ref=group_ref,
            limit=limit,
            after_id=cursor,
            created_after=after_dt,
            created_before=before_dt,
        ):
            _print_entry(entry)


@app.command()
def search(
    ctx: typer.Context,
    query_text: Annotated[
        str,
        typer.Argument(metavar="QUERY", help="Query text."),
    ],
    db: Db = None,
    mode: Annotated[
        str,
        typer.Option(
            help=(
                "Search mode: 'vector' for semantic similarity (default), "
                "'keyword' for FTS5 BM25 ranking. Different rankings, "
                "different result sets — pick the one that matches your query."
            ),
        ),
    ] = "vector",
    group_key: GroupKey = None,
    after: After = None,
    before: Before = None,
    k: Annotated[int, typer.Option(help="Number of results to return.")] = 10,
    dynamic_threshold: Annotated[
        bool,
        typer.Option(
            "--dynamic-threshold",
            help=(
                "Filter results by each entry's stored similarity threshold. "
                "Vector mode only."
            ),
        ),
    ] = False,
) -> None:
    """Run a vector or keyword search against a database in the mount."""
    if mode not in ("vector", "keyword"):
        _fail("--mode must be 'vector' or 'keyword'")
    if mode == "keyword" and dynamic_threshold:
        _fail("--dynamic-threshold is only valid with --mode vector")
    mount: Path = ctx.obj["mount"]
    after_dt = _parse_iso("--after", after)
    before_dt = _parse_iso("--before", before)
    with _open_grimoire(mount, db) as g:
        if mode == "vector":
            results = g.vector_search(
                query_text,
                group_key=group_key,
                k=k,
                dynamic_threshold=dynamic_threshold,
                created_after=after_dt,
                created_before=before_dt,
            )
        else:
            results = g.keyword_search(
                query_text,
                group_key=group_key,
                k=k,
                created_after=after_dt,
                created_before=before_dt,
            )
        for entry in results:
            _print_entry(entry)


@app.command(name="import")
def import_records(
    ctx: typer.Context,
    file: Annotated[
        Path,
        typer.Argument(
            help="Path to a JSONL file. One JSON object per line.",
            exists=True,
            file_okay=True,
            dir_okay=False,
            readable=True,
        ),
    ],
    db: Db = None,
) -> None:
    """Bulk-import records into a database from a JSONL file.

    Additive: records are appended to the existing database. Collisions on
    `(group_key, group_ref)` raise an error and abort the import — the file
    must be free of conflicts with existing records, or the conflicting
    records must be removed/updated first.
    """
    mount: Path = ctx.obj["mount"]
    records = _load_records(file)
    if not records:
        typer.echo(f"No records to import from {file}")
        return

    total = 0
    last_milestone = 0
    with _open_grimoire(mount, db) as g:
        for chunk_start in range(0, len(records), IMPORT_BATCH_SIZE):
            chunk = records[chunk_start : chunk_start + IMPORT_BATCH_SIZE]
            try:
                g.add_many(chunk)
            except sqlite3.IntegrityError as exc:
                _fail(f"collision in batch starting at record {chunk_start + 1}: {exc}")
            total += len(chunk)
            milestone = total // PROGRESS_EVERY
            if milestone > last_milestone and total < len(records):
                typer.echo(f"  imported {total}...", err=True)
                last_milestone = milestone

    typer.echo(f"Imported {len(records)} records into {_db_path(mount, db)}")


@app.command()
def export(
    ctx: typer.Context,
    db: Db = None,
    output: Annotated[
        Path | None,
        typer.Option(
            "--output",
            "-o",
            help=(
                "Output JSONL path. Defaults to <mount>/export.jsonl. "
                "Refuses to overwrite an existing file unless --force is set."
            ),
        ),
    ] = None,
    force: Annotated[
        bool,
        typer.Option("--force", help="Overwrite the output path if it exists."),
    ] = False,
) -> None:
    """Export every entry in a database to a JSONL file.

    The output format mirrors `import`'s expected input — entries can be
    round-tripped (content, group_key, group_ref, payload, threshold,
    keywords are preserved). Ids are NOT preserved on round-trip; they're
    grimoire-assigned and re-imported records get fresh ULIDs.
    """
    mount: Path = ctx.obj["mount"]
    if output is None:
        output = mount / EXPORT_FILENAME
    if output.exists() and not force:
        _fail(f"{output} exists; pass --force to overwrite")

    output.parent.mkdir(parents=True, exist_ok=True)
    total = 0
    with (
        _open_grimoire(mount, db) as g,
        output.open("w", encoding="utf-8") as f,
    ):
        cursor: str | None = None
        while True:
            batch = g.list(limit=500, after_id=cursor)
            if not batch:
                break
            for entry in batch:
                json.dump(_export_record(entry), f)
                f.write("\n")
            total += len(batch)
            cursor = batch[-1].id
    typer.echo(f"Exported {total} records to {output}")


@app.command()
def destroy(
    ctx: typer.Context,
    name: Annotated[
        str | None,
        typer.Argument(
            help=(
                "Name of the database to destroy. Omit to destroy the default "
                "database at <mount>/grimoire.db. Use `grimoire mount destroy` "
                "to wipe the entire mount."
            ),
        ),
    ] = None,
    yes: Annotated[
        bool,
        typer.Option(
            "--yes",
            "-y",
            help="Skip the interactive confirmation prompt.",
        ),
    ] = False,
) -> None:
    """Delete a single database from the mount.

    Without NAME, drops the default database at <mount>/grimoire.db. With
    NAME, drops <mount>/<name>/grimoire.db and removes its manifest entry.
    Idempotent — missing files or manifest entries are tolerated.
    """
    mount: Path = ctx.obj["mount"]
    db_file = _db_path(mount, name)
    if not db_file.exists():
        label = "default database" if name is None else f"database {name!r}"
        typer.echo(f"Nothing to destroy: no {label} at {db_file}")
        return
    if not yes:
        label = "the default database" if name is None else f"database {name!r}"
        typer.confirm(
            f"Permanently destroy {label} at {db_file}?",
            abort=True,
        )
    try:
        Grimoire.destroy(name, mount=mount)
    except (GrimoireError, InvalidMount) as exc:
        _fail(str(exc))
    if name is None:
        typer.echo(f"Destroyed default database at {db_file}")
    else:
        typer.echo(f"Destroyed database {name!r}")


@mount_app.command("destroy")
def mount_destroy(
    ctx: typer.Context,
    yes: Annotated[
        bool,
        typer.Option(
            "--yes",
            "-y",
            help="Skip the interactive confirmation prompt.",
        ),
    ] = False,
) -> None:
    """Destroy the entire mount: every database, the manifest, the model cache.

    There is no undo. Use `grimoire destroy [NAME]` for per-database removal.
    """
    mount: Path = ctx.obj["mount"]
    if not mount.exists():
        typer.echo(f"Nothing to destroy: {mount} does not exist")
        return
    if not yes:
        typer.confirm(
            f"Permanently destroy the entire mount at {mount} and everything under it?",
            abort=True,
        )
    handle = Grimoire.mount(mount)
    handle.destroy()
    typer.echo(f"Destroyed mount at {mount}")


@app.command()
def add(
    ctx: typer.Context,
    content: Annotated[str, typer.Argument(help="Content text for the new entry.")],
    db: Db = None,
    group_key: Annotated[
        str | None,
        typer.Option("--group-key", help="Group label for partitioning."),
    ] = None,
    group_ref: Annotated[
        str | None,
        typer.Option(
            "--group-ref",
            help=(
                "Consumer-set unique reference within the group. "
                "Collisions on (group_key, group_ref) raise an error."
            ),
        ),
    ] = None,
    payload: Annotated[
        str | None,
        typer.Option(help="Optional JSON object to attach as the entry payload."),
    ] = None,
    threshold: Annotated[
        float | None,
        typer.Option(help="Optional per-entry similarity threshold."),
    ] = None,
    keyword: Annotated[
        list[str] | None,
        typer.Option(
            "--keyword",
            help=(
                "Add an explicit search keyword to boost recall in keyword "
                "search. Repeatable: --keyword foo --keyword bar."
            ),
        ),
    ] = None,
) -> None:
    """Add a single record to a database in the mount."""
    mount: Path = ctx.obj["mount"]
    payload_obj: dict | None = None
    if payload is not None:
        try:
            parsed = json.loads(payload)
        except json.JSONDecodeError as exc:
            _fail(f"--payload is not valid JSON: {exc.msg}")
        if not isinstance(parsed, dict):
            _fail("--payload must be a JSON object")
        payload_obj = parsed
    with _open_grimoire(mount, db) as g:
        try:
            entry = g.add(
                content=content,
                group_key=group_key,
                group_ref=group_ref,
                payload=payload_obj,
                threshold=threshold,
                keywords=keyword or None,
            )
        except sqlite3.IntegrityError as exc:
            _fail(f"collision on (group_key, group_ref): {exc}")
    _print_entry(entry)


@app.command()
def update(
    ctx: typer.Context,
    entry_id: Annotated[str, typer.Argument(help="Entry id (ULID).")],
    db: Db = None,
    content: Annotated[
        str | None,
        typer.Option(help="Replace the entry's content (re-embeds and re-indexes)."),
    ] = None,
    group_key: Annotated[
        str | None,
        typer.Option("--group-key", help="Replace the entry's group_key."),
    ] = None,
    clear_group_key: Annotated[
        bool,
        typer.Option("--clear-group-key", help="Clear the group_key (set to NULL)."),
    ] = False,
    group_ref: Annotated[
        str | None,
        typer.Option("--group-ref", help="Replace the entry's group_ref."),
    ] = None,
    clear_group_ref: Annotated[
        bool,
        typer.Option("--clear-group-ref", help="Clear the group_ref (set to NULL)."),
    ] = False,
    payload: Annotated[
        str | None,
        typer.Option(help="Replace the payload with this JSON object."),
    ] = None,
    clear_payload: Annotated[
        bool,
        typer.Option("--clear-payload", help="Clear the payload (set to NULL)."),
    ] = False,
    threshold: Annotated[
        float | None,
        typer.Option(help="Replace the per-entry similarity threshold."),
    ] = None,
    clear_threshold: Annotated[
        bool,
        typer.Option("--clear-threshold", help="Clear the threshold (set to NULL)."),
    ] = False,
    keyword: Annotated[
        list[str] | None,
        typer.Option(
            "--keyword",
            help=(
                "Replace the keyword list. Repeatable: --keyword foo "
                "--keyword bar. Use --clear-keywords to remove all keywords."
            ),
        ),
    ] = None,
    clear_keywords: Annotated[
        bool,
        typer.Option("--clear-keywords", help="Clear the keyword list (set to NULL)."),
    ] = False,
) -> None:
    """Patch fields on an existing entry. Omitted fields are left unchanged."""
    if group_key is not None and clear_group_key:
        _fail("--group-key and --clear-group-key are mutually exclusive")
    if group_ref is not None and clear_group_ref:
        _fail("--group-ref and --clear-group-ref are mutually exclusive")
    if payload is not None and clear_payload:
        _fail("--payload and --clear-payload are mutually exclusive")
    if threshold is not None and clear_threshold:
        _fail("--threshold and --clear-threshold are mutually exclusive")
    if keyword is not None and clear_keywords:
        _fail("--keyword and --clear-keywords are mutually exclusive")

    # Build kwargs that distinguish "unset" (omit) from "set to None" (clear)
    # by simply not passing the key when the user didn't supply anything.
    kwargs: dict[str, object] = {}
    if content is not None:
        kwargs["content"] = content
    if clear_group_key:
        kwargs["group_key"] = None
    elif group_key is not None:
        kwargs["group_key"] = group_key
    if clear_group_ref:
        kwargs["group_ref"] = None
    elif group_ref is not None:
        kwargs["group_ref"] = group_ref
    if clear_payload:
        kwargs["payload"] = None
    elif payload is not None:
        try:
            parsed = json.loads(payload)
        except json.JSONDecodeError as exc:
            _fail(f"--payload is not valid JSON: {exc.msg}")
        if not isinstance(parsed, dict):
            _fail("--payload must be a JSON object")
        kwargs["payload"] = parsed
    if clear_threshold:
        kwargs["threshold"] = None
    elif threshold is not None:
        kwargs["threshold"] = threshold
    if clear_keywords:
        kwargs["keywords"] = None
    elif keyword is not None:
        kwargs["keywords"] = keyword

    mount: Path = ctx.obj["mount"]
    with _open_grimoire(mount, db) as g:
        try:
            entry = g.update(entry_id, **kwargs)
        except sqlite3.IntegrityError as exc:
            _fail(f"collision on (group_key, group_ref): {exc}")
        if entry is None:
            _fail(f"No entry with id {entry_id!r}")
        _print_entry(entry)


@app.command()
def get(
    ctx: typer.Context,
    entry_id: Annotated[str, typer.Argument(help="Entry id (ULID).")],
    db: Db = None,
) -> None:
    """Fetch a single entry by id."""
    mount: Path = ctx.obj["mount"]
    with _open_grimoire(mount, db) as g:
        entry = g.get(entry_id)
        if entry is None:
            _fail(f"No entry with id {entry_id!r}")
        _print_entry(entry)


@app.command()
def delete(
    ctx: typer.Context,
    entry_id: Annotated[str, typer.Argument(help="Entry id (ULID).")],
    db: Db = None,
) -> None:
    """Delete an entry by id."""
    mount: Path = ctx.obj["mount"]
    with _open_grimoire(mount, db) as g:
        if not g.delete(entry_id):
            _fail(f"No entry with id {entry_id!r}")
    typer.echo(f"Deleted {entry_id}")


# --- helpers ---------------------------------------------------------------


def _open_grimoire(mount: Path, name: str | None) -> Grimoire:
    """Open a database under `mount`, auto-loading its embedder.

    Surfaces `GrimoireNotFound` as a friendly "run grimoire init first" error
    and `InvalidMount` as a usage error for malformed names.
    """
    try:
        return Grimoire.open(name, mount=mount)
    except GrimoireNotFound:
        label = "default database" if name is None else f"database {name!r}"
        _fail(
            f"no {label} at {_db_path(mount, name)}; "
            f"run 'grimoire init{' --db ' + name if name else ''}' first"
        )
    except ImportError as exc:
        _fail(str(exc))
    except (GrimoireError, InvalidMount) as exc:
        _fail(str(exc))


def _emit_info(db: Path, *, label: str) -> None:
    """Print a one-line JSON summary for the database at `db`. Errors if missing."""
    stats = Grimoire.peek(db)
    if stats is None:
        _fail(f"No grimoire ({label}) at {db}")
    typer.echo(
        json.dumps(
            {
                "path": str(db),
                "model": stats.model,
                "dimension": stats.dimension,
                "schema_version": stats.schema_version,
                "entry_count": stats.entry_count,
                "groups": stats.groups,
            }
        )
    )


def _load_records(path: Path) -> list[dict]:
    records: list[dict] = []
    with path.open(encoding="utf-8") as f:
        for line_no, raw in enumerate(f, 1):
            line = raw.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                _fail(f"{path}:{line_no}: invalid JSON: {exc.msg}")
            _validate_record(record, path, line_no)
            records.append(record)
    return records


def _validate_record(record: object, path: Path, line_no: int) -> None:
    if not isinstance(record, dict):
        _fail(f"{path}:{line_no}: record must be a JSON object")
    missing = REQUIRED_FIELDS - record.keys()
    if missing:
        _fail(f"{path}:{line_no}: missing required fields: {sorted(missing)}")
    unknown = record.keys() - RECOGNIZED_FIELDS
    if unknown:
        _fail(
            f"{path}:{line_no}: unknown fields {sorted(unknown)}. "
            f"Put extra metadata in `payload`."
        )


def _entry_record(entry: Entry) -> dict[str, object]:
    """Build the JSON shape printed by read commands.

    Includes id and any non-null fields, plus distance/rank when the entry
    came from a search result. Mirrors the on-disk shape minus the columns
    that aren't set.
    """
    record: dict[str, object] = {"id": entry.id, "content": entry.content}
    if entry.group_key is not None:
        record["group_key"] = entry.group_key
    if entry.group_ref is not None:
        record["group_ref"] = entry.group_ref
    if entry.keywords is not None:
        record["keywords"] = entry.keywords
    if entry.payload is not None:
        record["payload"] = entry.payload
    if entry.threshold is not None:
        record["threshold"] = entry.threshold
    if entry.distance is not None:
        record["distance"] = entry.distance
    if entry.rank is not None:
        record["rank"] = entry.rank
    return record


def _export_record(entry: Entry) -> dict[str, object]:
    """Build the JSON shape written by `export` (round-trippable through `import`).

    Drops `id` (grimoire-assigned, re-imported records get fresh ULIDs) and
    drops result-only fields (`distance`, `rank`).
    """
    record: dict[str, object] = {"content": entry.content}
    if entry.group_key is not None:
        record["group_key"] = entry.group_key
    if entry.group_ref is not None:
        record["group_ref"] = entry.group_ref
    if entry.keywords is not None:
        record["keywords"] = entry.keywords
    if entry.payload is not None:
        record["payload"] = entry.payload
    if entry.threshold is not None:
        record["threshold"] = entry.threshold
    return record


def _print_entry(entry: Entry) -> None:
    typer.echo(json.dumps(_entry_record(entry)))


def _parse_iso(flag: str, value: str | None) -> datetime | None:
    if value is None:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        _fail(f"{flag} must be ISO 8601 (e.g. 2026-05-04 or 2026-05-04T10:00:00)")


def _fail(message: str) -> NoReturn:
    typer.echo(f"Error: {message}", err=True)
    raise typer.Exit(code=1)


def main() -> None:
    """Console-script entrypoint for the `grimoire` CLI."""
    app()


if __name__ == "__main__":
    main()
