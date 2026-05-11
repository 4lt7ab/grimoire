import json
from pathlib import Path
from typing import Annotated

import typer
from grimoire.data.entry import Entry, Filters
from grimoire.grimoire import open as grim_open

app = typer.Typer(name="grimoire", no_args_is_help=False)
entry_app = typer.Typer(name="entry", no_args_is_help=True)
app.add_typer(entry_app)


@app.callback(invoke_without_command=True)
def _root(ctx: typer.Context) -> None:
    if ctx.invoked_subcommand is None:
        typer.echo("grimoire")


DbOpt = Annotated[Path, typer.Option("--db", help="Path to the SQLite file.")]


@entry_app.command("add")
def entry_add(
    db: DbOpt,
    group_key: Annotated[str | None, typer.Option("--group-key")] = None,
    group_ref: Annotated[str | None, typer.Option("--group-ref")] = None,
    payload: Annotated[
        str | None,
        typer.Option("--payload", help="JSON object."),
    ] = None,
) -> None:
    payload_obj = json.loads(payload) if payload is not None else None
    with grim_open(db) as g:
        [saved] = g.add([Entry(None, group_key, group_ref, payload_obj)])
    typer.echo(json.dumps(_entry_to_dict(saved)))


@entry_app.command("remove")
def entry_remove(
    db: DbOpt,
    ids: Annotated[list[str], typer.Argument(help="One or more entry IDs.")],
) -> None:
    with grim_open(db) as g:
        removed = g.remove(ids)
    for r in removed:
        typer.echo(r)


@entry_app.command("fetch")
def entry_fetch(
    db: DbOpt,
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
    filters = Filters(id=id, group_key=group_key, group_ref=group_ref)
    with grim_open(db) as g:
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
