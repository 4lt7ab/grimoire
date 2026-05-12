import json
from dataclasses import asdict
from typing import Annotated

from grimoire.data.entry import Entry, Filters
import typer

from grimoire import grimoire
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


@mount_app.command(name="create")
def mount_create_cmd() -> None:
    """Create the mount + default DB. Idempotent."""
    mnt = mount.resolve()
    mount.create(mnt)

    with grimoire.open(mnt.default_db, embedder=embed.build_embedder(mnt.models_dir)):
        pass

    typer.echo(json.dumps(asdict(mnt), indent=2, default=str))


@mount_app.command(name="destroy")
def mount_destroy_cmd(
    yes: Annotated[
        bool,
        typer.Option("--yes", help="Confirm destruction of the entire mount."),
    ] = False,
) -> None:
    """Wipe the entire mount — every DB, registry, and model cache. No undo."""
    if not yes:
        raise typer.BadParameter("Pass --yes to confirm destruction of the mount.")

    mnt = mount.resolve()
    if not mnt.exists():
        raise typer.BadParameter("No mount to destroy.")

    mount.destroy(mnt)

    typer.echo(json.dumps({"path": str(mnt.path), "destroyed": True}, indent=2))


@mount_app.command(name="add")
def mount_add_cmd(
    name: Annotated[
        str,
        typer.Argument(help="Name of the database to add."),
    ],
) -> None:
    """Add a named grimoire database to the mount."""
    mnt = mount.resolve()
    if not mnt.exists():
        raise typer.BadParameter("Mount does not exist; run `grimoire mount create` first.")

    db_path = mnt.db_path(name)
    db_path.parent.mkdir(exist_ok=True)

    with grimoire.open(db_path, embedder=embed.build_embedder(mnt.models_dir)):
        pass

    typer.echo(json.dumps({"name": name, "path": str(db_path)}, indent=2))


@mount_app.command(name="ls")
def mount_ls_cmd() -> None:
    """List databases in the mount."""
    mnt = mount.resolve()
    if not mnt.exists():
        raise typer.BadParameter("Mount does not exist; run `grimoire mount create` first.")

    dbs: list[dict[str, str | None]] = [{"name": None, "path": str(mnt.default_db)}]
    for sub in sorted(mnt.path.iterdir()):
        if not sub.is_dir():
            continue
        db = sub / mount.DB_FILENAME
        if db.is_file():
            dbs.append({"name": sub.name, "path": str(db)})

    typer.echo(json.dumps(dbs, indent=2))


@mount_app.command(name="remove")
def mount_remove_cmd(
    name: Annotated[
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

    mnt = mount.resolve()
    if not mnt.exists():
        raise typer.BadParameter("Mount does not exist; run `grimoire mount create` first.")

    db_path = mnt.db_path(name)
    if not db_path.exists():
        raise typer.BadParameter(f"No database named {name!r}.")

    db_path.unlink()
    try:
        db_path.parent.rmdir()
    except OSError:
        pass

    typer.echo(json.dumps({"name": name, "removed": True}, indent=2))


@entry_app.command(name="add")
def entry_add_cmd(
    semantic_text: Annotated[
        str | None,
        typer.Argument(help="Text embedded for semantic search."),
    ] = None,
    name: Annotated[
        str | None,
        typer.Option("--name", "-n", help="Database name (default DB if omitted)."),
    ] = None,
    keyword_text: Annotated[
        str | None,
        typer.Option("--keyword-text", "-k", help="Text indexed for FTS5 keyword search."),
    ] = None,
    group_key: Annotated[
        str | None,
        typer.Option("--group-key", help="Group key partition for this entry."),
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
    threshold_rank: Annotated[
        float | None,
        typer.Option("--threshold-rank", help="Minimum BM25 rank score for keyword hits."),
    ] = None,
    threshold_distance: Annotated[
        float | None,
        typer.Option("--threshold-distance", help="Maximum vector distance for semantic hits."),
    ] = None,
) -> None:
    """Create a Grimoire entry."""
    mnt = mount.resolve()
    if not mnt.exists():
        raise typer.BadParameter("Mount does not exist; run `grimoire mount create` first.")

    db_path = mnt.db_path(name)
    if not db_path.exists():
        target = f"database {name!r}" if name else "default database"
        raise typer.BadParameter(f"No {target} in the mount.")

    payload_data = json.loads(payload) if payload is not None else None

    with grimoire.open(db_path, embedder=embed.build_embedder(mnt.models_dir)) as g:
        [created] = g.add([Entry(
            id=None,
            group_key=group_key,
            group_ref=group_ref,
            payload=payload_data,
            context=context,
            keyword_text=keyword_text,
            semantic_text=semantic_text,
            threshold_rank=threshold_rank,
            threshold_distance=threshold_distance,
        )])

    typer.echo(json.dumps(asdict(created), indent=2, default=str))


@entry_app.command(name="fetch")
def entry_fetch_cmd(
    name: Annotated[
        str | None,
        typer.Option("--name", "-n", help="Database name (default DB if omitted)."),
    ] = None,
    ids: Annotated[
        list[str] | None,
        typer.Option("--id", help="Filter to entries with these ids. Repeatable."),
    ] = None,
    group_keys: Annotated[
        list[str] | None,
        typer.Option("--group-key", help="Filter to entries with these group keys. Repeatable."),
    ] = None,
    group_refs: Annotated[
        list[str] | None,
        typer.Option("--group-ref", help="Filter to entries with these group refs. Repeatable."),
    ] = None,
    limit: Annotated[
        int,
        typer.Option("--limit", help="Maximum entries to return."),
    ] = 100,
) -> None:
    """Fetch Grimoire entries matching the given filters."""
    mnt = mount.resolve()
    if not mnt.exists():
        raise typer.BadParameter("Mount does not exist; run `grimoire mount create` first.")

    db_path = mnt.db_path(name)
    if not db_path.exists():
        target = f"database {name!r}" if name else "default database"
        raise typer.BadParameter(f"No {target} in the mount.")

    filters = Filters(
        id=ids or None,
        group_key=group_keys or None,
        group_ref=group_refs or None,
    )

    with grimoire.open(db_path, embedder=embed.build_embedder(mnt.models_dir)) as g:
        entries = g.fetch(filters, limit=limit)

    typer.echo(json.dumps([asdict(e) for e in entries], indent=2, default=str))


@entry_app.command(name="delete")
def entry_delete_cmd(
    entry_id: Annotated[
        str,
        typer.Argument(help="Id of the entry to delete."),
    ],
    name: Annotated[
        str | None,
        typer.Option("--name", "-n", help="Database name (default DB if omitted)."),
    ] = None,
    yes: Annotated[
        bool,
        typer.Option("--yes", help="Confirm deletion."),
    ] = False,
) -> None:
    """Delete a Grimoire entry by id. Idempotent — missing ids return deleted=false."""
    if not yes:
        raise typer.BadParameter("Pass --yes to confirm deletion.")

    mnt = mount.resolve()
    if not mnt.exists():
        raise typer.BadParameter("Mount does not exist; run `grimoire mount create` first.")

    db_path = mnt.db_path(name)
    if not db_path.exists():
        target = f"database {name!r}" if name else "default database"
        raise typer.BadParameter(f"No {target} in the mount.")

    with grimoire.open(db_path, embedder=embed.build_embedder(mnt.models_dir)) as g:
        removed = g.remove([entry_id])

    typer.echo(json.dumps({"id": entry_id, "deleted": bool(removed)}, indent=2))
