import contextlib
import json
import re
from dataclasses import asdict, replace
from importlib.metadata import version
from pathlib import Path
from typing import Annotated

import typer
from grimoire.data.entry import Entry, Filters
from grimoire.grimoire import Grimoire

from grimoire_cli import embed, mount

app = typer.Typer(
    name="grimoire",
    no_args_is_help=True,
    add_completion=False,
    help="Operate on a SQLite + sqlite-vec grimoire mount.",
)

mount_app = typer.Typer(
    name="mount",
    no_args_is_help=True,
    add_completion=False,
    help="Operate on the grimoire mount and the databases within it.",
)
app.add_typer(mount_app)

entry_app = typer.Typer(
    name="entry",
    no_args_is_help=True,
    add_completion=False,
    help="Operate on entries within a grimoire database.",
)
app.add_typer(entry_app)

search_app = typer.Typer(
    name="search",
    no_args_is_help=True,
    add_completion=False,
    help="Search a database — keyword (FTS5 BM25) or semantic (vec0 KNN).",
)
app.add_typer(search_app)

mcp_app = typer.Typer(
    name="mcp",
    no_args_is_help=True,
    add_completion=False,
    help="Expose the mount over the Model Context Protocol.",
)
app.add_typer(mcp_app)


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(version("4lt7ab-grimoire-cli"))
        raise typer.Exit()


@app.callback()
def main(
    ctx: typer.Context,
    mount_path: Annotated[
        Path | None,
        typer.Option(
            "--mount",
            help=(
                "Path to the grimoire mount (overrides $GRIMOIRE_MOUNT;"
                " default ~/.grimoire)."
            ),
        ),
    ] = None,
    _version: Annotated[
        bool,
        typer.Option(
            "--version",
            help="Show the grimoire CLI version and exit.",
            callback=_version_callback,
            is_eager=True,
        ),
    ] = False,
) -> None:
    ctx.obj = mount.resolve(mount_path)


def _existing_mount(ctx: typer.Context) -> mount.Mount:
    mnt: mount.Mount = ctx.obj
    if not mnt.exists():
        raise typer.BadParameter(
            "Mount does not exist; run `grimoire mount create` first."
        )
    return mnt


@mount_app.command(name="create")
def mount_create_cmd(ctx: typer.Context) -> None:
    """Create the mount + default DB. Idempotent."""
    mnt: mount.Mount = ctx.obj
    mount.create(mnt)

    with Grimoire.open(mnt.default_db, embedder=embed.build_embedder(mnt.models_dir)):
        pass

    typer.echo(json.dumps(asdict(mnt), indent=2, default=str))


@mount_app.command(name="destroy")
def mount_destroy_cmd(
    ctx: typer.Context,
    yes: Annotated[
        bool,
        typer.Option("--yes", help="Confirm destruction of the entire mount."),
    ] = False,
) -> None:
    """Wipe the entire mount — every DB, registry, and model cache. No undo."""
    if not yes:
        raise typer.BadParameter("Pass --yes to confirm destruction of the mount.")

    mnt: mount.Mount = ctx.obj
    if not mnt.exists():
        raise typer.BadParameter("No mount to destroy.")

    mount.destroy(mnt)

    typer.echo(json.dumps({"path": str(mnt.path), "destroyed": True}, indent=2))


@mount_app.command(name="add")
def mount_add_cmd(
    ctx: typer.Context,
    db: Annotated[
        str,
        typer.Argument(help="Name of the database to add."),
    ],
) -> None:
    """Add a named grimoire database to the mount."""
    mnt = _existing_mount(ctx)

    try:
        db_path = mnt.db_path(db)
    except ValueError as e:
        raise typer.BadParameter(str(e)) from e
    db_path.parent.mkdir(exist_ok=True)

    with Grimoire.open(db_path, embedder=embed.build_embedder(mnt.models_dir)):
        pass

    typer.echo(json.dumps({"db": db_path.parent.name, "path": str(db_path)}, indent=2))


@mount_app.command(name="ls")
def mount_ls_cmd(ctx: typer.Context) -> None:
    """List databases in the mount."""
    mnt = _existing_mount(ctx)

    dbs: list[dict[str, str | None]] = [{"db": None, "path": str(mnt.default_db)}]
    for sub in sorted(mnt.path.iterdir()):
        if not sub.is_dir():
            continue
        db_file = sub / mount.DB_FILENAME
        if db_file.is_file():
            dbs.append({"db": sub.name, "path": str(db_file)})

    typer.echo(json.dumps(dbs, indent=2))


@mount_app.command(name="remove")
def mount_remove_cmd(
    ctx: typer.Context,
    db: Annotated[
        str,
        typer.Argument(help="Name of the database to remove."),
    ],
    yes: Annotated[
        bool,
        typer.Option("--yes", help="Confirm deletion."),
    ] = False,
) -> None:
    """Remove a named grimoire database from the mount."""
    if not yes:
        raise typer.BadParameter("Pass --yes to confirm deletion.")

    mnt = _existing_mount(ctx)

    try:
        db_path = mnt.db_path(db)
    except ValueError as e:
        raise typer.BadParameter(str(e)) from e
    if not db_path.exists():
        raise typer.BadParameter(f"No database named {db!r}.")

    db_path.unlink()
    with contextlib.suppress(OSError):
        db_path.parent.rmdir()

    typer.echo(json.dumps({"db": db_path.parent.name, "removed": True}, indent=2))


@entry_app.command(name="add")
def entry_add_cmd(
    ctx: typer.Context,
    db: Annotated[
        str | None,
        typer.Option("--db", "-d", help="Database name (default DB if omitted)."),
    ] = None,
    group_key: Annotated[
        str | None,
        typer.Option("--group-key", help="Group key metadata for this entry."),
    ] = None,
    group_ref: Annotated[
        str | None,
        typer.Option("--group-ref", help="External reference id within the group."),
    ] = None,
    context: Annotated[
        str | None,
        typer.Option("--context", help="Unindexed contextual text."),
    ] = None,
    payload: Annotated[
        str | None,
        typer.Option("--payload", help="JSON payload object."),
    ] = None,
    ordinal: Annotated[
        float | None,
        typer.Option(
            "--ordinal",
            help=(
                "Numeric sort key for consumer-defined ordering (timestamps,"
                " section numbers, measurements). Indexed; nullable."
            ),
        ),
    ] = None,
    keyword_text: Annotated[
        str | None,
        typer.Option(
            "--keyword-text", help="Keyword text to index in FTS5 for this entry."
        ),
    ] = None,
    threshold_rank: Annotated[
        float | None,
        typer.Option(
            "--threshold-rank",
            help="Minimum BM25 score for keyword hits (non-negative).",
            min=0,
        ),
    ] = None,
    semantic_text: Annotated[
        str | None,
        typer.Option(
            "--semantic-text", help="Semantic text to embed and store on the vec row."
        ),
    ] = None,
    partition: Annotated[
        str | None,
        typer.Option(
            "--partition",
            help="Vec partition to write into. Omit for the NULL partition.",
        ),
    ] = None,
    threshold_distance: Annotated[
        float | None,
        typer.Option(
            "--threshold-distance",
            help="Maximum vector distance for semantic hits (non-negative).",
            min=0,
        ),
    ] = None,
) -> None:
    """Create a Grimoire entry, optionally indexing it in one go.

    Pass `--keyword-text` to add an FTS5 row and `--semantic-text` to embed
    a vec row. Either, both, or neither — the entry is always created.
    """
    if keyword_text is None and threshold_rank is not None:
        raise typer.BadParameter("--threshold-rank requires --keyword-text.")
    if semantic_text is None and (
        partition is not None or threshold_distance is not None
    ):
        raise typer.BadParameter(
            "--partition and --threshold-distance require --semantic-text."
        )

    mnt = _existing_mount(ctx)

    try:
        db_path = mnt.db_path(db)
    except ValueError as e:
        raise typer.BadParameter(str(e)) from e
    if not db_path.exists():
        target = f"database {db!r}" if db else "default database"
        raise typer.BadParameter(f"No {target} in the mount.")

    try:
        payload_data = json.loads(payload) if payload is not None else None
    except json.JSONDecodeError as e:
        raise typer.BadParameter(f"Invalid JSON payload: {e.msg}") from e

    with Grimoire.open(db_path, embedder=embed.build_embedder(mnt.models_dir)) as g:
        try:
            [created] = g.add(
                [
                    Entry(
                        id=None,
                        group_key=group_key,
                        group_ref=group_ref,
                        payload=payload_data,
                        context=context,
                        ordinal=ordinal,
                    )
                ]
            )
            if keyword_text is not None:
                [created] = g.keyword(
                    [(created.id, keyword_text)], threshold_rank=threshold_rank
                )
            if semantic_text is not None:
                [created] = g.embed(
                    [(created.id, semantic_text)],
                    partition=partition,
                    threshold_distance=threshold_distance,
                )
        except ValueError as e:
            raise typer.BadParameter(str(e)) from e

    typer.echo(json.dumps(asdict(created), indent=2, default=str))


@entry_app.command(name="update")
def entry_update_cmd(
    ctx: typer.Context,
    entry_id: Annotated[
        str,
        typer.Argument(help="Id of the entry to update."),
    ],
    db: Annotated[
        str | None,
        typer.Option("--db", "-d", help="Database name (default DB if omitted)."),
    ] = None,
    group_key: Annotated[
        str | None,
        typer.Option("--group-key", help="Group key metadata for this entry."),
    ] = None,
    group_ref: Annotated[
        str | None,
        typer.Option("--group-ref", help="External reference id within the group."),
    ] = None,
    payload: Annotated[
        str | None,
        typer.Option("--payload", help="JSON payload object."),
    ] = None,
    context: Annotated[
        str | None,
        typer.Option("--context", help="Unindexed contextual text."),
    ] = None,
    ordinal: Annotated[
        float | None,
        typer.Option(
            "--ordinal",
            help=(
                "Numeric sort key for consumer-defined ordering. In --put mode,"
                " omit to clear; in partial mode, omit to preserve."
            ),
        ),
    ] = None,
    keyword_text: Annotated[
        str | None,
        typer.Option(
            "--keyword-text",
            help="Replace the entry's FTS5 row with this keyword text.",
        ),
    ] = None,
    threshold_rank: Annotated[
        float | None,
        typer.Option(
            "--threshold-rank",
            help="Minimum BM25 score for keyword hits (non-negative).",
            min=0,
        ),
    ] = None,
    semantic_text: Annotated[
        str | None,
        typer.Option(
            "--semantic-text",
            help="Replace the entry's vec row with this semantic text.",
        ),
    ] = None,
    partition: Annotated[
        str | None,
        typer.Option(
            "--partition",
            help="Vec partition to write into. Omit for the NULL partition.",
        ),
    ] = None,
    threshold_distance: Annotated[
        float | None,
        typer.Option(
            "--threshold-distance",
            help="Maximum vector distance for semantic hits (non-negative).",
            min=0,
        ),
    ] = None,
    put: Annotated[
        bool,
        typer.Option(
            "--put",
            help=(
                "Replace the entry's mutable fields wholesale. Any field not "
                "given is set to NULL. Destructive — pair every field you want "
                "to keep with its current value."
            ),
        ),
    ] = False,
) -> None:
    """Update an entry; optionally (re-)index its keyword or semantic text.

    Default mode is partial-update: unspecified entry fields are preserved.
    Pass `--put` to switch to replace mode for the entry fields (group_key,
    group_ref, payload, context).

    Indexing is decoupled from `--put`: passing `--keyword-text` always
    replaces the FTS5 row, and `--semantic-text` always replaces the vec row.
    Leaving them off preserves the existing index rows as-is.
    """
    if keyword_text is None and threshold_rank is not None:
        raise typer.BadParameter("--threshold-rank requires --keyword-text.")
    if semantic_text is None and (
        partition is not None or threshold_distance is not None
    ):
        raise typer.BadParameter(
            "--partition and --threshold-distance require --semantic-text."
        )

    mnt = _existing_mount(ctx)

    try:
        db_path = mnt.db_path(db)
    except ValueError as e:
        raise typer.BadParameter(str(e)) from e
    if not db_path.exists():
        target = f"database {db!r}" if db else "default database"
        raise typer.BadParameter(f"No {target} in the mount.")

    payload_provided = payload is not None
    try:
        payload_value = json.loads(payload) if payload_provided else None
    except json.JSONDecodeError as e:
        raise typer.BadParameter(f"Invalid JSON payload: {e.msg}") from e

    with Grimoire.open(db_path, embedder=embed.build_embedder(mnt.models_dir)) as g:
        existing = g.fetch(Filters(id=[entry_id]), limit=1)
        if not existing:
            raise typer.BadParameter(f"No entry with id {entry_id!r}.")
        current = existing[0]

        if put:
            merged = Entry(
                id=current.id,
                group_key=group_key,
                group_ref=group_ref,
                payload=payload_value if payload_provided else None,
                context=context,
                ordinal=ordinal,
            )
        else:
            merged = replace(
                current,
                group_key=current.group_key if group_key is None else group_key,
                group_ref=current.group_ref if group_ref is None else group_ref,
                payload=payload_value if payload_provided else current.payload,
                context=current.context if context is None else context,
                ordinal=current.ordinal if ordinal is None else ordinal,
            )

        try:
            [returned] = g.update([merged])
            if keyword_text is not None:
                g.keyword([(returned.id, keyword_text)], threshold_rank=threshold_rank)
            if semantic_text is not None:
                g.embed(
                    [(returned.id, semantic_text)],
                    partition=partition,
                    threshold_distance=threshold_distance,
                )
        except ValueError as e:
            raise typer.BadParameter(str(e)) from e

        [returned] = g.fetch(Filters(id=[returned.id]), limit=1)

    typer.echo(json.dumps(asdict(returned), indent=2, default=str))


@entry_app.command(name="get")
def entry_get_cmd(
    ctx: typer.Context,
    entry_id: Annotated[
        str,
        typer.Argument(help="Id of the entry to fetch."),
    ],
    db: Annotated[
        str | None,
        typer.Option("--db", "-d", help="Database name (default DB if omitted)."),
    ] = None,
) -> None:
    """Fetch a single Grimoire entry by id."""
    mnt = _existing_mount(ctx)

    try:
        db_path = mnt.db_path(db)
    except ValueError as e:
        raise typer.BadParameter(str(e)) from e
    if not db_path.exists():
        target = f"database {db!r}" if db else "default database"
        raise typer.BadParameter(f"No {target} in the mount.")

    with Grimoire.open(db_path, embedder=embed.build_embedder(mnt.models_dir)) as g:
        entries = g.fetch(Filters(id=[entry_id]), limit=1)
    if not entries:
        raise typer.BadParameter(f"No entry with id {entry_id!r}.")

    typer.echo(json.dumps(asdict(entries[0]), indent=2, default=str))


def _human_size(n: int) -> str:
    size = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024:
            return f"{int(size)} {unit}" if size == int(size) else f"{size:.1f} {unit}"
        size /= 1024
    return f"{int(size)} TB" if size == int(size) else f"{size:.1f} TB"


@app.command(name="info")
def info_cmd(
    ctx: typer.Context,
    db: Annotated[
        str | None,
        typer.Option("--db", "-d", help="Database name (default DB if omitted)."),
    ] = None,
) -> None:
    """Show metadata for a grimoire database.

    Embedder lock, schema version, counts, and file size.
    """
    mnt = _existing_mount(ctx)

    try:
        db_path = mnt.db_path(db)
    except ValueError as e:
        raise typer.BadParameter(str(e)) from e
    if not db_path.exists():
        target = f"database {db!r}" if db else "default database"
        raise typer.BadParameter(f"No {target} in the mount.")

    peeked = Grimoire.peek(db_path)
    size_bytes = db_path.stat().st_size
    result = {
        "db": db,
        "path": str(db_path),
        "size_bytes": size_bytes,
        "size": _human_size(size_bytes),
        **asdict(peeked),
    }
    typer.echo(json.dumps(result, indent=2, default=str))


@app.command(name="fetch")
def fetch_cmd(
    ctx: typer.Context,
    db: Annotated[
        str | None,
        typer.Option("--db", "-d", help="Database name (default DB if omitted)."),
    ] = None,
    ids: Annotated[
        list[str] | None,
        typer.Option("--id", help="Filter to entries with these ids. Repeatable."),
    ] = None,
    group_keys: Annotated[
        list[str] | None,
        typer.Option(
            "--group-key", help="Filter to entries with these group keys. Repeatable."
        ),
    ] = None,
    group_refs: Annotated[
        list[str] | None,
        typer.Option(
            "--group-ref", help="Filter to entries with these group refs. Repeatable."
        ),
    ] = None,
    ordinal_gte: Annotated[
        float | None,
        typer.Option(
            "--ordinal-gte",
            help="Restrict to entries with ordinal >= this value.",
        ),
    ] = None,
    ordinal_lte: Annotated[
        float | None,
        typer.Option(
            "--ordinal-lte",
            help="Restrict to entries with ordinal <= this value.",
        ),
    ] = None,
    cursor: Annotated[
        str | None,
        typer.Option(
            "--cursor",
            help=(
                "Return entries with id > this."
                " Pass the id of the last entry from the previous page."
            ),
        ),
    ] = None,
    order_by: Annotated[
        str,
        typer.Option(
            "--order-by",
            help="Sort column: 'id' (chronological) or 'ordinal'.",
        ),
    ] = "id",
    desc: Annotated[
        bool,
        typer.Option(
            "--desc",
            help="Sort descending instead of ascending.",
        ),
    ] = False,
    limit: Annotated[
        int,
        typer.Option("--limit", help="Maximum entries to return.", min=0),
    ] = 100,
) -> None:
    """Fetch Grimoire entries matching the given filters.

    Default order is by id (chronological, since ids are ULIDs); pass
    `--order-by ordinal` to sort by the consumer-supplied ordinal column
    instead. NULL ordinals sort last. `--desc` reverses direction.

    For paging, pass `--cursor <id>` where `<id>` is the last entry's id from
    the previous page. Cursor is id-based; pair it with the default
    `--order-by id` (ascending). For ordinal-window paging, use
    `--ordinal-gte` / `--ordinal-lte`.
    """
    if order_by not in {"id", "ordinal"}:
        raise typer.BadParameter("--order-by must be 'id' or 'ordinal'.")

    mnt = _existing_mount(ctx)

    try:
        db_path = mnt.db_path(db)
    except ValueError as e:
        raise typer.BadParameter(str(e)) from e
    if not db_path.exists():
        target = f"database {db!r}" if db else "default database"
        raise typer.BadParameter(f"No {target} in the mount.")

    filters = Filters(
        id=ids or None,
        group_key=group_keys or None,
        group_ref=group_refs or None,
        ordinal_gte=ordinal_gte,
        ordinal_lte=ordinal_lte,
    )

    with Grimoire.open(db_path, embedder=embed.build_embedder(mnt.models_dir)) as g:
        entries = g.fetch(
            filters,
            limit=limit,
            cursor=cursor,
            order_by=order_by,  # type: ignore[arg-type]
            descending=desc,
        )

    typer.echo(json.dumps([asdict(e) for e in entries], indent=2, default=str))


@search_app.command(name="keyword")
def search_keyword_cmd(
    ctx: typer.Context,
    query: Annotated[
        str,
        typer.Argument(help="Search query — parsed as FTS5."),
    ],
    db: Annotated[
        str | None,
        typer.Option("--db", "-d", help="Database name (default DB if omitted)."),
    ] = None,
    group_keys: Annotated[
        list[str] | None,
        typer.Option(
            "--group-key", help="Filter to entries with these group keys. Repeatable."
        ),
    ] = None,
    group_refs: Annotated[
        list[str] | None,
        typer.Option(
            "--group-ref", help="Filter to entries with these group refs. Repeatable."
        ),
    ] = None,
    ids: Annotated[
        list[str] | None,
        typer.Option("--id", help="Filter to entries with these ids. Repeatable."),
    ] = None,
    ordinal_gte: Annotated[
        float | None,
        typer.Option(
            "--ordinal-gte",
            help="Restrict hits to entries with ordinal >= this value.",
        ),
    ] = None,
    ordinal_lte: Annotated[
        float | None,
        typer.Option(
            "--ordinal-lte",
            help="Restrict hits to entries with ordinal <= this value.",
        ),
    ] = None,
    limit: Annotated[
        int,
        typer.Option("--limit", help="Maximum hits.", min=0),
    ] = 10,
) -> None:
    """Keyword search via FTS5 BM25.

    Supports filtering by group_key, group_ref, id, and ordinal range.
    `rank` is the BM25 score (higher = better, non-negative).
    """
    mnt = _existing_mount(ctx)

    try:
        db_path = mnt.db_path(db)
    except ValueError as e:
        raise typer.BadParameter(str(e)) from e
    if not db_path.exists():
        target = f"database {db!r}" if db else "default database"
        raise typer.BadParameter(f"No {target} in the mount.")

    filters = Filters(
        id=ids or None,
        group_key=group_keys or None,
        group_ref=group_refs or None,
        ordinal_gte=ordinal_gte,
        ordinal_lte=ordinal_lte,
    )
    # Quote-wrap each word token so apostrophes, punctuation, and bareword FTS5
    # operators (AND/OR/NOT/NEAR/*) can't reach the parser. Join with OR so
    # casual prose matches any-of, not all-of; BM25 still ranks by aggregate
    # match strength.
    fts_query = " OR ".join(f'"{t}"' for t in re.findall(r"\w+", query))

    with Grimoire.open(db_path, embedder=embed.build_embedder(mnt.models_dir)) as g:
        hits = (
            g.keyword_search(fts_query, filters=filters, limit=limit)
            if fts_query
            else []
        )

    result = [{"entry": asdict(h.entry), "rank": h.score} for h in hits]
    typer.echo(json.dumps(result, indent=2, default=str))


@search_app.command(name="semantic")
def search_semantic_cmd(
    ctx: typer.Context,
    query: Annotated[
        str,
        typer.Argument(help="Search query — embedded for vec0 KNN."),
    ],
    db: Annotated[
        str | None,
        typer.Option("--db", "-d", help="Database name (default DB if omitted)."),
    ] = None,
    partition: Annotated[
        str | None,
        typer.Option(
            "--partition",
            help=(
                "Restrict semantic hits to this vec partition."
                " Omit to search every partition."
            ),
        ),
    ] = None,
    limit: Annotated[
        int,
        typer.Option("--limit", help="Maximum hits.", min=0),
    ] = 10,
) -> None:
    """Semantic search via vec0 KNN, narrowable by partition.

    `distance` is the raw vector distance (lower = better, non-negative).
    """
    mnt = _existing_mount(ctx)

    try:
        db_path = mnt.db_path(db)
    except ValueError as e:
        raise typer.BadParameter(str(e)) from e
    if not db_path.exists():
        target = f"database {db!r}" if db else "default database"
        raise typer.BadParameter(f"No {target} in the mount.")

    with Grimoire.open(db_path, embedder=embed.build_embedder(mnt.models_dir)) as g:
        hits = g.semantic_search(query, partition=partition, limit=limit)

    result = [{"entry": asdict(h.entry), "distance": h.distance} for h in hits]
    typer.echo(json.dumps(result, indent=2, default=str))


@entry_app.command(name="delete")
def entry_delete_cmd(
    ctx: typer.Context,
    entry_id: Annotated[
        str,
        typer.Argument(help="Id of the entry to delete."),
    ],
    db: Annotated[
        str | None,
        typer.Option("--db", "-d", help="Database name (default DB if omitted)."),
    ] = None,
    yes: Annotated[
        bool,
        typer.Option("--yes", help="Confirm deletion."),
    ] = False,
) -> None:
    """Delete a Grimoire entry by id. Idempotent — missing ids return deleted=false."""
    if not yes:
        raise typer.BadParameter("Pass --yes to confirm deletion.")

    mnt = _existing_mount(ctx)

    try:
        db_path = mnt.db_path(db)
    except ValueError as e:
        raise typer.BadParameter(str(e)) from e
    if not db_path.exists():
        target = f"database {db!r}" if db else "default database"
        raise typer.BadParameter(f"No {target} in the mount.")

    with Grimoire.open(db_path, embedder=embed.build_embedder(mnt.models_dir)) as g:
        removed = g.remove([entry_id])

    typer.echo(json.dumps({"id": entry_id, "deleted": bool(removed)}, indent=2))


@mcp_app.command(name="serve")
def mcp_serve_cmd(ctx: typer.Context) -> None:
    """Run the grimoire MCP server over stdio against this mount."""
    from grimoire_cli import mcp as mcp_module

    mnt = _existing_mount(ctx)
    mcp_module.build_server(mnt).run()
