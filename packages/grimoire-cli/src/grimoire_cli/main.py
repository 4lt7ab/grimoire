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
    Mount,
    MountNotFound,
)

from grimoire_cli.output import (
    emit_db_info,
    emit_entries,
    emit_entry,
    emit_listing,
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
    "vector_text",
    "keyword_text",
    "payload",
    "threshold",
}
# Both `vector_text` and `keyword_text` are optional — an entry can opt into
# vector search, keyword search, both, or neither. The schema enforces no
# minimum, and neither does the import.
REQUIRED_FIELDS: set[str] = set()


# Reusable annotations — every command shares the mount lookup, and read commands
# share filters/paging. Defining them once keeps help text in lockstep.
MountOpt = Annotated[
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

entry_app = typer.Typer(
    name="entry",
    no_args_is_help=True,
    pretty_exceptions_enable=False,
    help=(
        "Operate on entries within a database — add, get, update, delete, "
        "and bulk import/export. Hot-path reads (`grimoire search`, "
        "`grimoire query`) stay at the top level."
    ),
)
app.add_typer(entry_app)


@app.callback(invoke_without_command=True)
def _callback(
    ctx: typer.Context,
    mount: MountOpt = None,
    raw: Annotated[
        bool,
        typer.Option(
            "--raw",
            help=(
                "Force JSONL output even at a terminal. By default, output is "
                "pretty when stdout is a TTY and JSONL when piped — pass --raw "
                "to keep the JSONL shape interactively (e.g. for inspection)."
            ),
        ),
    ] = False,
) -> None:
    """Manage a grimoire datastore — a single-file SQLite + sqlite-vec semantic store.

    With no subcommand, prints metadata for the default database in the mount
    (model, dimension, schema version, entry count, per-group counts).

    Every command operates over a mount directory that holds the SQLite files
    and the shared embedder model cache. A mount can hold one default database
    at <mount>/grimoire.db plus any number of named databases under
    <mount>/<name>/grimoire.db, tracked in <mount>/grimoire.toml. Specify the
    mount with --mount <dir> or set GRIMOIRE_MOUNT; defaults to ~/.grimoire.
    Pick a database within the mount with --db <name>.

    Read commands (query, search, get) print a pretty table at the terminal
    and JSONL when piped. Pass --raw to force JSONL at the terminal.
    """
    resolved = Mount.resolve(mount)
    ctx.obj = {"mount": resolved, "raw": raw}
    if ctx.invoked_subcommand is None:
        _emit_info(resolved, name=None, label="default database", raw=raw)


@mount_app.callback(invoke_without_command=True)
def _mount_callback(
    ctx: typer.Context,
    model: Annotated[
        str | None,
        typer.Option(
            help=(
                "fastembed model name. Used only when first creating the "
                "default database; passing it against an existing database "
                "whose locked model differs is an error."
            ),
        ),
    ] = None,
) -> None:
    """Set up the mount with its default database; report what's in the mount.

    With no subcommand, ensures the mount directory and shared model cache
    exist, creates the default database at <mount>/grimoire.db if missing,
    then prints the same JSONL listing as `grimoire ls`. Idempotent — re-running
    on a mount whose default database already exists skips the embedder load
    entirely and just prints the listing.

    Subcommands operate on the mount as a whole. `grimoire mount destroy`
    wipes the entire mount.
    """
    if ctx.invoked_subcommand is not None:
        return

    mount_path: Path = ctx.obj["mount"]
    mount = Mount(mount_path, create=True)  # ensures mount root + models/ exist

    if not mount.has():
        # No default DB yet — create it. This is the only path that needs
        # fastembed at all; re-runs against an existing default DB skip it.
        model_name = model or DEFAULT_MODEL
        try:
            from grimoire.embedders import FastembedEmbedder

            embedder = FastembedEmbedder(model_name, cache_folder=mount.models_path)
        except ImportError as exc:
            _fail(str(exc))
        try:
            Grimoire(mount=mount, embedder=embedder).close()
        except GrimoireError as exc:
            _fail(str(exc))
    elif model is not None:
        stats = mount.peek()
        if stats is not None and model != stats.model:
            _fail(
                f"file is locked to model {stats.model!r}; "
                f"drop --model or use a different --mount"
            )

    _emit_listing(mount, raw=ctx.obj["raw"])


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
    mount_path: Path = ctx.obj["mount"]
    try:
        mount = Mount(mount_path, create=True)
    except InvalidMount as exc:
        _fail(str(exc))
    try:
        if mount.has(name):
            _fail(f"database {name!r} already exists at {mount.path_for(name)}")
    except InvalidMount as exc:
        _fail(str(exc))

    model_name = model or DEFAULT_MODEL
    try:
        from grimoire.embedders import FastembedEmbedder

        embedder = FastembedEmbedder(model_name, cache_folder=mount.models_path)
    except ImportError as exc:
        _fail(str(exc))

    try:
        Grimoire(
            name,
            mount=mount,
            embedder=embedder,
            description=description,
        ).close()
    except (GrimoireError, InvalidMount) as exc:
        _fail(str(exc))

    _emit_info(mount_path, name=name, label=f"database {name!r}", raw=ctx.obj["raw"])


@app.command()
def ls(ctx: typer.Context) -> None:
    """List databases in the mount.

    Default database (if present) is listed first, then named databases in
    alphabetical order. Pretty table at the terminal; JSONL when piped or
    with --raw — one object per database (name null for default).
    """
    mount_path: Path = ctx.obj["mount"]
    try:
        mount = Mount(mount_path)
    except MountNotFound:
        emit_listing([], raw=ctx.obj["raw"])
        return
    _emit_listing(mount, raw=ctx.obj["raw"])


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
        entries = g.list(
            group_key=group_key,
            group_ref=group_ref,
            limit=limit,
            after_id=cursor,
            created_after=after_dt,
            created_before=before_dt,
        )
    emit_entries(entries, raw=ctx.obj["raw"])


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
    k: Annotated[
        int,
        typer.Option("-k", "--k", help="Number of results to return."),
    ] = 10,
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
    emit_entries(results, raw=ctx.obj["raw"])


@entry_app.command(name="import")
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

    typer.echo(f"Imported {len(records)} records into {Mount(mount).path_for(db)}")


@entry_app.command()
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
    round-tripped (vector_text, keyword_text, group_key, group_ref, payload,
    and threshold are preserved). Ids are NOT preserved on round-trip;
    they're grimoire-assigned and re-imported records get fresh ULIDs.
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
    mount_path: Path = ctx.obj["mount"]
    label = "default database" if name is None else f"database {name!r}"
    try:
        mount = Mount(mount_path)
    except MountNotFound:
        typer.echo(f"Nothing to destroy: no mount at {mount_path}")
        return
    try:
        if not mount.has(name):
            typer.echo(f"Nothing to destroy: no {label} at {mount.path_for(name)}")
            return
    except InvalidMount as exc:
        _fail(str(exc))
    db_file = mount.path_for(name)
    if not yes:
        prompt_label = "the default database" if name is None else f"database {name!r}"
        typer.confirm(
            f"Permanently destroy {prompt_label} at {db_file}?",
            abort=True,
        )
    try:
        mount.drop(name)
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
    mount_path: Path = ctx.obj["mount"]
    try:
        mount = Mount(mount_path)
    except MountNotFound:
        typer.echo(f"Nothing to destroy: {mount_path} does not exist")
        return
    if not yes:
        typer.confirm(
            f"Permanently destroy the entire mount at {mount_path} "
            f"and everything under it?",
            abort=True,
        )
    mount.destroy()
    typer.echo(f"Destroyed mount at {mount_path}")


@entry_app.command()
def add(
    ctx: typer.Context,
    vector_text: Annotated[
        str | None,
        typer.Argument(
            help=(
                "Free-form text to embed and index for vector_search. Pass "
                "an empty string or omit to skip the vector index entirely "
                "(entry is invisible to vector search but still retrievable "
                "by id / group_ref / list)."
            ),
        ),
    ] = None,
    db: Db = None,
    keyword_text: Annotated[
        str | None,
        typer.Option(
            "--keyword-text",
            help=(
                "Free-form text to index for keyword_search via FTS5 BM25. "
                "Omit to skip the keyword index. Whatever string you pass is "
                "tokenized as-is — no list shape, no special syntax."
            ),
        ),
    ] = None,
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
) -> None:
    """Add a single record to a database in the mount.

    Both `--vector-text` and `--keyword-text` are optional and independent.
    Supply either, both, or neither.
    """
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
                vector_text=vector_text,
                keyword_text=keyword_text,
                group_key=group_key,
                group_ref=group_ref,
                payload=payload_obj,
                threshold=threshold,
            )
        except sqlite3.IntegrityError as exc:
            _fail(f"collision on (group_key, group_ref): {exc}")
    emit_entry(entry, raw=ctx.obj["raw"])


@entry_app.command()
def update(
    ctx: typer.Context,
    entry_id: Annotated[str, typer.Argument(help="Entry id (ULID).")],
    db: Db = None,
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
) -> None:
    """Patch the mutable metadata fields on an existing entry.

    Only `payload` and `threshold` can be updated. Indexed and identity
    fields (`vector_text`, `keyword_text`, `group_key`, `group_ref`) are
    immutable after creation — to change them, delete the entry and add
    a fresh one.
    """
    if payload is not None and clear_payload:
        _fail("--payload and --clear-payload are mutually exclusive")
    if threshold is not None and clear_threshold:
        _fail("--threshold and --clear-threshold are mutually exclusive")

    # Build kwargs that distinguish "unset" (omit) from "set to None" (clear)
    # by simply not passing the key when the user didn't supply anything.
    kwargs: dict[str, object] = {}
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

    mount: Path = ctx.obj["mount"]
    with _open_grimoire(mount, db) as g:
        entry = g.update(entry_id, **kwargs)
        if entry is None:
            _fail(f"No entry with id {entry_id!r}")
        emit_entry(entry, raw=ctx.obj["raw"])


@entry_app.command()
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
        emit_entry(entry, raw=ctx.obj["raw"])


@entry_app.command()
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

    Surfaces `GrimoireNotFound` as a friendly "create it first" error and
    `InvalidMount` as a usage error for malformed names.
    """
    try:
        return Grimoire(name, mount=mount)
    except GrimoireNotFound:
        label = "default database" if name is None else f"database {name!r}"
        suggest = "grimoire mount" if name is None else f"grimoire create {name}"
        _fail(f"no {label} in {mount}; run '{suggest}' first")
    except ImportError as exc:
        _fail(str(exc))
    except (GrimoireError, InvalidMount) as exc:
        _fail(str(exc))


def _emit_info(mount_path: Path, *, name: str | None, label: str, raw: bool) -> None:
    """Print a database summary; pretty at TTY, JSON otherwise. Errors if missing."""
    try:
        mount = Mount(mount_path)
    except MountNotFound:
        _fail(f"No grimoire ({label}) at {mount_path}")
    stats = mount.peek(name)
    if stats is None:
        _fail(f"No grimoire ({label}) at {mount.path_for(name)}")
    emit_db_info(mount.path_for(name), stats, raw=raw)


def _emit_listing(mount: Mount, *, raw: bool) -> None:
    """Print one row per database in the mount; shared by `mount` and `ls`."""
    emit_listing(mount.list(), raw=raw)


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


def _export_record(entry: Entry) -> dict[str, object]:
    """Build the JSON shape written by `export` (round-trippable through `import`).

    Drops `id` (grimoire-assigned, re-imported records get fresh ULIDs) and
    drops result-only fields (`distance`, `rank`). Both `vector_text` and
    `keyword_text` are emitted only when set — a record with neither is a
    valid payload-only entry and round-trips as such.
    """
    record: dict[str, object] = {}
    if entry.vector_text is not None:
        record["vector_text"] = entry.vector_text
    if entry.keyword_text is not None:
        record["keyword_text"] = entry.keyword_text
    if entry.group_key is not None:
        record["group_key"] = entry.group_key
    if entry.group_ref is not None:
        record["group_ref"] = entry.group_ref
    if entry.payload is not None:
        record["payload"] = entry.payload
    if entry.threshold is not None:
        record["threshold"] = entry.threshold
    return record


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
