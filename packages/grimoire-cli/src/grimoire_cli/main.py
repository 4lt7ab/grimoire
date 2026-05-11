from __future__ import annotations

import functools
import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated

import click
import typer
from grimoire.data.entry import Entry, Filters
from grimoire.errors import GrimoireError
from grimoire.grimoire import open as open_grimoire
from grimoire.grimoire import peek

from grimoire_cli import manifest
from grimoire_cli.resolve import (
    Kind,
    Mount,
    make_embedder_for_create,
    open_db,
    require_mount,
    resolve_mount,
)


class _CliFail(click.ClickException):
    """Wraps a `GrimoireError` so click prints it cleanly and exits 1."""

    exit_code = 1

    def format_message(self) -> str:
        return self.message


def _catches(fn):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except GrimoireError as e:
            raise _CliFail(str(e)) from e

    return wrapper


app = typer.Typer(name="grimoire", no_args_is_help=False, add_completion=False)
mount_app = typer.Typer(
    name="mount", invoke_without_command=True, no_args_is_help=False
)
entry_app = typer.Typer(name="entry", no_args_is_help=True)
app.add_typer(mount_app)
app.add_typer(entry_app)


@dataclass(frozen=True, slots=True)
class Settings:
    mount: Mount
    db: str | None


MountOpt = Annotated[
    Path | None,
    typer.Option(
        "--mount",
        envvar="GRIMOIRE_MOUNT",
        help="Mount directory. Default: ~/.grimoire.",
        show_default=False,
    ),
]
DbOpt = Annotated[
    str | None,
    typer.Option(
        "--db",
        "-d",
        envvar="GRIMOIRE_DB",
        help="Named database. Default: the mount's anonymous default DB.",
        show_default=False,
    ),
]
EmbedderOpt = Annotated[
    Kind,
    typer.Option(
        "--embedder",
        envvar="GRIMOIRE_EMBEDDER",
        help="Embedder kind for the new DB.",
    ),
]
ModelOpt = Annotated[
    str | None,
    typer.Option(
        "--model",
        envvar="GRIMOIRE_MODEL",
        help="Model name for fastembed. Ignored for noop.",
        show_default=False,
    ),
]
YesOpt = Annotated[
    bool,
    typer.Option("--yes", help="Confirm a destructive action."),
]


@app.callback(invoke_without_command=True)
@_catches
def _root(
    ctx: typer.Context,
    mount: MountOpt = None,
    db: DbOpt = None,
) -> None:
    ctx.obj = Settings(mount=resolve_mount(mount), db=db)
    if ctx.invoked_subcommand is None:
        _bare(ctx.obj)


def _bare(settings: Settings) -> None:
    require_mount(settings.mount)
    path = settings.mount.db_path(settings.db)
    info = peek(path)
    typer.echo(
        json.dumps(
            {
                "mount": str(settings.mount.path),
                "db": settings.db,
                "path": str(path),
                "model": info.model,
                "dimension": info.dimension,
                "schema_version": info.schema_version,
                "entry_count": info.entry_count,
                "group_counts": {
                    (k if k is not None else ""): v
                    for k, v in info.group_counts.items()
                },
            }
        )
    )


@mount_app.callback(invoke_without_command=True)
@_catches
def _mount_root(
    ctx: typer.Context,
    embedder: EmbedderOpt = Kind.noop,
    model: ModelOpt = None,
) -> None:
    if ctx.invoked_subcommand is not None:
        return
    settings: Settings = ctx.obj
    _create_mount(settings.mount, embedder, model)


def _create_mount(mount: Mount, kind: Kind, model: str | None) -> None:
    mount.path.mkdir(parents=True, exist_ok=True)
    mount.models_dir.mkdir(parents=True, exist_ok=True)
    manifest.init(mount.path)
    em = make_embedder_for_create(kind, model, mount)
    fresh = not mount.default_db.exists()
    g = open_grimoire(mount.default_db, embedder=em)
    if fresh:
        em.embed(" ")
    g._conn.commit()
    g._conn.close()
    state = "Created" if fresh else "Mount already initialized at"
    typer.echo(f"{state} {mount.path}")


@mount_app.command("destroy")
@_catches
def mount_destroy(ctx: typer.Context, yes: YesOpt = False) -> None:
    if not yes:
        raise GrimoireError("Pass --yes to confirm wiping the mount.")
    mount: Mount = ctx.obj.mount
    if not mount.exists():
        typer.echo(f"No mount at {mount.path}")
        return
    shutil.rmtree(mount.path)
    typer.echo(f"Destroyed {mount.path}")


@entry_app.command("add")
@_catches
def entry_add(
    ctx: typer.Context,
    group_key: Annotated[str | None, typer.Option("--group-key")] = None,
    group_ref: Annotated[str | None, typer.Option("--group-ref")] = None,
    payload: Annotated[
        str | None,
        typer.Option("--payload", help="JSON object."),
    ] = None,
) -> None:
    settings: Settings = ctx.obj
    payload_obj = json.loads(payload) if payload is not None else None
    with open_db(settings.mount, settings.db) as g:
        [saved] = g.add([Entry(None, group_key, group_ref, payload_obj)])
    typer.echo(json.dumps(_entry_to_dict(saved)))


@entry_app.command("remove")
@_catches
def entry_remove(
    ctx: typer.Context,
    ids: Annotated[list[str], typer.Argument(help="One or more entry IDs.")],
) -> None:
    settings: Settings = ctx.obj
    with open_db(settings.mount, settings.db) as g:
        removed = g.remove(ids)
    for r in removed:
        typer.echo(r)


@entry_app.command("fetch")
@_catches
def entry_fetch(
    ctx: typer.Context,
    id: Annotated[
        list[str] | None,
        typer.Option("--id", help="Filter to these IDs (repeatable)."),
    ] = None,
    group_key: Annotated[
        list[str] | None,
        typer.Option("--group-key", help="Filter to these group_keys (repeatable)."),
    ] = None,
    group_ref: Annotated[
        list[str] | None,
        typer.Option("--group-ref", help="Filter to these group_refs (repeatable)."),
    ] = None,
) -> None:
    settings: Settings = ctx.obj
    filters = Filters(id=id, group_key=group_key, group_ref=group_ref)
    with open_db(settings.mount, settings.db) as g:
        entries = g.fetch(filters)
    for e in entries:
        typer.echo(json.dumps(_entry_to_dict(e)))


def _entry_to_dict(e: Entry) -> dict:
    return {
        "id": e.id,
        "group_key": e.group_key,
        "group_ref": e.group_ref,
        "payload": e.payload,
    }


def main() -> None:
    app()


if __name__ == "__main__":
    main()
