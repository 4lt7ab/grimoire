import json
from datetime import datetime
from pathlib import Path
from typing import Annotated, NoReturn

import typer
from grimoire import Entry, Grimoire, GrimoireError, GrimoireNotFound

RECOGNIZED_FIELDS = {"kind", "content", "payload", "threshold", "keywords"}
REQUIRED_FIELDS = {"kind", "content"}
UPDATE_RECOGNIZED_FIELDS = {"id", *RECOGNIZED_FIELDS}
UPDATE_REQUIRED_FIELDS = {"id"}
# Each batch is one atomic transaction; on failure, only the in-flight batch
# rolls back. Smaller = better recovery granularity, slightly more overhead.
# 200 captures ~95% of fastembed's batching speedup vs single calls.
INGEST_BATCH_SIZE = 200
PROGRESS_EVERY = 1000
DEFAULT_MODEL = "BAAI/bge-small-en-v1.5"
DB_FILENAME = "grimoire.db"
MODELS_DIRNAME = "models"


# Reusable annotations — every command needs --mount, and the read commands
# share --kind, --k, --created-after, and --created-before. Defining them
# once keeps help text in lockstep across the CLI.
def _normalize_mount(value: Path) -> Path:
    # Pathlib doesn't expand ~ on its own, and a literal ~ in cache_dir
    # propagates into ONNX Runtime as a missing-file error. Expand once,
    # at the boundary, so every downstream caller sees an absolute path.
    return Path(value).expanduser()


Mount = Annotated[
    Path,
    typer.Option(
        help="Path to the grimoire mount directory.",
        envvar="GRIMOIRE_MOUNT",
        callback=_normalize_mount,
    ),
]
Kind = Annotated[
    str | None,
    typer.Option(help="Restrict results to entries of this kind."),
]
K = Annotated[int, typer.Option(help="Number of results to return.")]
CreatedAfter = Annotated[
    str | None,
    typer.Option(
        "--created-after",
        help="ISO 8601 lower bound on entry creation time (inclusive).",
    ),
]
CreatedBefore = Annotated[
    str | None,
    typer.Option(
        "--created-before",
        help="ISO 8601 upper bound on entry creation time (exclusive).",
    ),
]


app = typer.Typer(
    name="grimoire",
    no_args_is_help=True,
    pretty_exceptions_enable=False,
    epilog=(
        "Environment variables:\n\n"
        "  GRIMOIRE_MOUNT  Default mount directory. Overridden by --mount."
    ),
)


@app.callback()
def _callback() -> None:
    """Manage a grimoire datastore — a single-file SQLite + sqlite-vec semantic store.

    Every command operates over a mount directory that holds the SQLite file
    (<mount>/grimoire.db) and the embedder model cache (<mount>/models/).
    Specify it with --mount <dir> or set the GRIMOIRE_MOUNT environment
    variable once for the shell.

    Read commands (search, list, get, info) print one JSON object per line —
    pipe to `jq` for filtering.

    Run `grimoire init` for one-time setup, then `grimoire <command> --help`
    for the flags and arguments of any subcommand.
    """


@app.command()
def init(
    mount: Mount,
    model: Annotated[
        str | None,
        typer.Option(
            help=(
                "fastembed model name. Used only when creating a new grimoire; "
                "passing this against an existing grimoire whose locked model "
                "differs is an error."
            ),
        ),
    ] = None,
) -> None:
    """Create or verify a grimoire and warm its embedder. One-time setup."""
    db = mount / DB_FILENAME
    cache_folder = mount / MODELS_DIRNAME
    mount.mkdir(parents=True, exist_ok=True)
    cache_folder.mkdir(parents=True, exist_ok=True)

    stats = Grimoire.peek(db)
    if stats is not None and model is not None and model != stats.model:
        _fail(
            f"file is locked to model {stats.model!r}; "
            f"drop --model or use a different --mount path"
        )

    model_name = stats.model if stats else (model or DEFAULT_MODEL)

    try:
        from grimoire.embedders import FastembedEmbedder

        embedder = FastembedEmbedder(model_name, cache_folder=cache_folder)
    except ImportError as exc:
        _fail(str(exc))

    try:
        Grimoire.init(db, embedder=embedder).close()
    except GrimoireError as exc:
        _fail(str(exc))

    _emit_info(db)


@app.command()
def ingest(
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
    mount: Mount,
) -> None:
    """Bulk-ingest records into a grimoire."""
    records = _load_records(file)
    if not records:
        typer.echo(f"No records to ingest from {file}")
        return

    total = 0
    last_milestone = 0
    with _open_grimoire(mount) as g:
        for chunk_start in range(0, len(records), INGEST_BATCH_SIZE):
            chunk = records[chunk_start : chunk_start + INGEST_BATCH_SIZE]
            g.add_many(chunk)
            total += len(chunk)
            milestone = total // PROGRESS_EVERY
            if milestone > last_milestone and total < len(records):
                typer.echo(f"  ingested {total}...", err=True)
                last_milestone = milestone

    typer.echo(f"Ingested {len(records)} records into {mount / DB_FILENAME}")


@app.command(name="vector-search")
def vector_search(
    query: Annotated[str, typer.Argument(help="Query text to embed and search for.")],
    mount: Mount,
    kind: Kind = None,
    k: K = 10,
    dynamic_threshold: Annotated[
        bool,
        typer.Option(
            "--dynamic-threshold",
            help="Filter results by each entry's stored similarity threshold.",
        ),
    ] = False,
    created_after: CreatedAfter = None,
    created_before: CreatedBefore = None,
) -> None:
    """Run a vector (semantic) search against a grimoire."""
    after = _parse_iso("--created-after", created_after)
    before = _parse_iso("--created-before", created_before)
    with _open_grimoire(mount) as g:
        for entry in g.vector_search(
            query,
            kind=kind,
            k=k,
            dynamic_threshold=dynamic_threshold,
            created_after=after,
            created_before=before,
        ):
            _print_entry(entry)


@app.command(name="keyword-search")
def keyword_search(
    query: Annotated[
        str,
        typer.Argument(
            help=(
                "FTS5 query string. Plain words match tokens; supports phrases, "
                "prefix matches, and boolean operators."
            ),
        ),
    ],
    mount: Mount,
    kind: Kind = None,
    k: K = 10,
    created_after: CreatedAfter = None,
    created_before: CreatedBefore = None,
) -> None:
    """Run a keyword (FTS5) search against a grimoire."""
    after = _parse_iso("--created-after", created_after)
    before = _parse_iso("--created-before", created_before)
    with _open_grimoire(mount) as g:
        for entry in g.keyword_search(
            query,
            kind=kind,
            k=k,
            created_after=after,
            created_before=before,
        ):
            _print_entry(entry)


@app.command()
def add(
    content: Annotated[str, typer.Argument(help="Content text for the new entry.")],
    mount: Mount,
    kind: Annotated[str, typer.Option(help="Kind label for the entry.")] = "note",
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
                "Add an explicit search keyword to boost recall in keyword-search. "
                "Repeatable: --keyword foo --keyword bar."
            ),
        ),
    ] = None,
) -> None:
    """Add a single record to a grimoire."""
    payload_obj: dict | None = None
    if payload is not None:
        try:
            parsed = json.loads(payload)
        except json.JSONDecodeError as exc:
            _fail(f"--payload is not valid JSON: {exc.msg}")
        if not isinstance(parsed, dict):
            _fail("--payload must be a JSON object")
        payload_obj = parsed
    with _open_grimoire(mount) as g:
        entry = g.add(
            kind=kind,
            content=content,
            payload=payload_obj,
            threshold=threshold,
            keywords=keyword or None,
        )
    _print_entry(entry)


@app.command()
def info(mount: Mount) -> None:
    """Show metadata and counts for a grimoire file."""
    _emit_info(mount / DB_FILENAME)


@app.command(name="list")
def list_entries(
    mount: Mount,
    kind: Kind = None,
    limit: Annotated[
        int, typer.Option(help="Maximum number of entries to return.")
    ] = 100,
    after_id: Annotated[
        str | None, typer.Option(help="Cursor: return entries with id > this value.")
    ] = None,
    created_after: CreatedAfter = None,
    created_before: CreatedBefore = None,
) -> None:
    """Paginate entries in chronological order (by id)."""
    after = _parse_iso("--created-after", created_after)
    before = _parse_iso("--created-before", created_before)
    with _open_grimoire(mount) as g:
        for entry in g.list(
            kind=kind,
            limit=limit,
            after_id=after_id,
            created_after=after,
            created_before=before,
        ):
            _print_entry(entry)


@app.command()
def get(
    entry_id: Annotated[str, typer.Argument(help="Entry id (ULID).")],
    mount: Mount,
) -> None:
    """Fetch a single entry by id."""
    with _open_grimoire(mount) as g:
        entry = g.get(entry_id)
        if entry is None:
            _fail(f"No entry with id {entry_id!r}")
        _print_entry(entry)


@app.command()
def update(
    entry_id: Annotated[str, typer.Argument(help="Entry id (ULID).")],
    mount: Mount,
    kind: Annotated[
        str | None,
        typer.Option(help="Replace the entry's kind label."),
    ] = None,
    content: Annotated[
        str | None,
        typer.Option(help="Replace the entry's content (re-embeds and re-indexes)."),
    ] = None,
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
                "Replace the keyword list. Repeatable: --keyword foo --keyword bar. "
                "Use --clear-keywords to remove all keywords."
            ),
        ),
    ] = None,
    clear_keywords: Annotated[
        bool,
        typer.Option("--clear-keywords", help="Clear the keyword list (set to NULL)."),
    ] = False,
) -> None:
    """Patch fields on an existing entry. Omitted fields are left unchanged."""
    if payload is not None and clear_payload:
        _fail("--payload and --clear-payload are mutually exclusive")
    if threshold is not None and clear_threshold:
        _fail("--threshold and --clear-threshold are mutually exclusive")
    if keyword is not None and clear_keywords:
        _fail("--keyword and --clear-keywords are mutually exclusive")

    # Build kwargs that distinguish "unset" (omit) from "set to None" (clear)
    # by simply not passing the key when the user didn't supply anything.
    kwargs: dict[str, object] = {}
    if kind is not None:
        kwargs["kind"] = kind
    if content is not None:
        kwargs["content"] = content
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

    with _open_grimoire(mount) as g:
        entry = g.update(entry_id, **kwargs)
        if entry is None:
            _fail(f"No entry with id {entry_id!r}")
        _print_entry(entry)


@app.command(name="update-many")
def update_many(
    file: Annotated[
        Path,
        typer.Argument(
            help=(
                "Path to a JSONL file. One JSON object per line; each must include "
                "`id` and may include `kind`, `content`, `payload`, `threshold`, "
                "`keywords`. Absent keys leave the field unchanged; an explicit "
                "`null` clears nullable fields."
            ),
            exists=True,
            file_okay=True,
            dir_okay=False,
            readable=True,
        ),
    ],
    mount: Mount,
) -> None:
    """Bulk-patch entries in a grimoire from a JSONL file."""
    records = _load_update_records(file)
    if not records:
        typer.echo(f"No records to update from {file}")
        return

    total = 0
    missing = 0
    last_milestone = 0
    with _open_grimoire(mount) as g:
        for chunk_start in range(0, len(records), INGEST_BATCH_SIZE):
            chunk = records[chunk_start : chunk_start + INGEST_BATCH_SIZE]
            for entry in g.update_many(chunk):
                total += 1
                if entry is None:
                    missing += 1
            milestone = total // PROGRESS_EVERY
            if milestone > last_milestone and total < len(records):
                typer.echo(f"  updated {total}...", err=True)
                last_milestone = milestone

    msg = f"Updated {len(records) - missing} of {len(records)} records"
    if missing:
        msg += f" ({missing} unknown id{'s' if missing != 1 else ''})"
    typer.echo(msg)


@app.command()
def delete(
    entry_id: Annotated[str, typer.Argument(help="Entry id (ULID).")],
    mount: Mount,
) -> None:
    """Delete an entry by id."""
    with _open_grimoire(mount) as g:
        if not g.delete(entry_id):
            _fail(f"No entry with id {entry_id!r}")
    typer.echo(f"Deleted {entry_id}")


@app.command(name="delete-many")
def delete_many(
    file: Annotated[
        Path,
        typer.Argument(
            help=(
                "Path to a file with one entry id per line. Pass '-' to read "
                "ids from stdin. Blank lines and lines starting with '#' are "
                "ignored."
            ),
        ),
    ],
    mount: Mount,
) -> None:
    """Bulk-delete entries by id."""
    ids = _load_ids(file)
    if not ids:
        typer.echo(f"No ids to delete from {file}")
        return

    with _open_grimoire(mount) as g:
        results = g.delete_many(ids)

    deleted = sum(1 for ok in results if ok)
    missing = len(results) - deleted
    msg = f"Deleted {deleted} of {len(results)} ids"
    if missing:
        msg += f" ({missing} unknown id{'s' if missing != 1 else ''})"
    typer.echo(msg)


def _open_grimoire(mount: Path) -> Grimoire:
    """Open the grimoire under `mount`, auto-detecting the model from the file.

    Surfaces `GrimoireNotFound` as a friendly "run grimoire init first" error.
    """
    db = mount / DB_FILENAME
    cache_folder = mount / MODELS_DIRNAME
    cache_folder.mkdir(parents=True, exist_ok=True)
    stats = Grimoire.peek(db)
    if stats is None:
        _fail(f"no grimoire at {db}; run 'grimoire init' first")
    try:
        from grimoire.embedders import FastembedEmbedder

        embedder = FastembedEmbedder(stats.model, cache_folder=cache_folder)
    except ImportError as exc:
        _fail(str(exc))
    try:
        return Grimoire.open(db, embedder=embedder)
    except GrimoireNotFound:
        _fail(f"no grimoire at {db}; run 'grimoire init' first")
    except GrimoireError as exc:
        _fail(str(exc))


def _emit_info(db: Path) -> None:
    stats = Grimoire.peek(db)
    if stats is None:
        _fail(f"No grimoire at {db}")
    typer.echo(
        json.dumps(
            {
                "path": str(db),
                "model": stats.model,
                "dimension": stats.dimension,
                "schema_version": stats.schema_version,
                "entry_count": stats.entry_count,
                "kinds": stats.kinds,
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


def _load_update_records(path: Path) -> list[dict]:
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
            _validate_update_record(record, path, line_no)
            records.append(record)
    return records


def _validate_update_record(record: object, path: Path, line_no: int) -> None:
    if not isinstance(record, dict):
        _fail(f"{path}:{line_no}: record must be a JSON object")
    missing = UPDATE_REQUIRED_FIELDS - record.keys()
    if missing:
        _fail(f"{path}:{line_no}: missing required fields: {sorted(missing)}")
    unknown = record.keys() - UPDATE_RECOGNIZED_FIELDS
    if unknown:
        _fail(
            f"{path}:{line_no}: unknown fields {sorted(unknown)}. "
            f"Put extra metadata in `payload`."
        )


def _load_ids(path: Path) -> list[str]:
    """Read entry ids one per line from `path`. '-' means stdin.

    Blank lines and `#`-prefixed comments are skipped, matching common
    pipeline conventions (e.g. `grimoire list | jq -r .id`).
    """
    if str(path) == "-":
        source = typer.get_text_stream("stdin")
    else:
        if not path.exists():
            _fail(f"file not found: {path}")
        source = path.open(encoding="utf-8")
    ids: list[str] = []
    try:
        for raw in source:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            ids.append(line)
    finally:
        if str(path) != "-":
            source.close()
    return ids


def _print_entry(entry: Entry) -> None:
    record: dict[str, object] = {
        "id": entry.id,
        "kind": entry.kind,
        "content": entry.content,
    }
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
    typer.echo(json.dumps(record))


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
