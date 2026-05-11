from __future__ import annotations

import functools
import json
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime
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
    SearchMode,
    make_embedder_for_create,
    open_db,
    require_mount,
    resolve_mount,
    validate_db_name,
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


@app.command("create")
@_catches
def create(
    ctx: typer.Context,
    name: Annotated[str, typer.Argument(help="Name of the new database.")],
    description: Annotated[
        str | None,
        typer.Option("--description", help="Optional description for the manifest."),
    ] = None,
    embedder: EmbedderOpt = Kind.noop,
    model: ModelOpt = None,
) -> None:
    validate_db_name(name)
    mount: Mount = ctx.obj.mount
    require_mount(mount)

    db_dir = mount.path / name
    if db_dir.exists() or name in manifest.read(mount.path):
        raise GrimoireError(f"DB {name!r} already exists.")

    db_dir.mkdir(parents=True)
    em = make_embedder_for_create(embedder, model, mount)
    g = open_grimoire(db_dir / "grimoire.db", embedder=em)
    em.embed(" ")
    g._conn.commit()
    g._conn.close()

    manifest.add(
        mount.path,
        manifest.DbRecord(
            name=name,
            model=em.model,
            created_at=_now_iso(),
            description=description,
        ),
    )
    typer.echo(f"Created {db_dir / 'grimoire.db'}")


@app.command("destroy")
@_catches
def destroy(
    ctx: typer.Context,
    name: Annotated[str, typer.Argument(help="Name of the database to remove.")],
    yes: YesOpt = False,
) -> None:
    validate_db_name(name)
    if not yes:
        raise GrimoireError(f"Pass --yes to confirm destroying DB {name!r}.")
    mount: Mount = ctx.obj.mount
    require_mount(mount)

    db_dir = mount.path / name
    records = manifest.read(mount.path)
    if not db_dir.exists() and name not in records:
        raise GrimoireError(f"No DB {name!r} at {mount.path}.")

    if db_dir.exists():
        shutil.rmtree(db_dir)
    manifest.remove(mount.path, name)
    typer.echo(f"Destroyed DB {name!r}")


@app.command("ls")
@_catches
def ls(ctx: typer.Context) -> None:
    mount: Mount = ctx.obj.mount
    require_mount(mount)

    rows: list[dict[str, object]] = []
    if mount.default_db.exists():
        info = peek(mount.default_db)
        rows.append(_db_row(name=None, path=mount.default_db, info=info))

    records = manifest.read(mount.path)
    for name in sorted(records):
        rec = records[name]
        path = mount.path / name / "grimoire.db"
        info = peek(path) if path.exists() else None
        rows.append(_db_row(name=name, path=path, info=info, manifest_record=rec))

    for row in rows:
        typer.echo(json.dumps(row))


def _db_row(
    *,
    name: str | None,
    path: Path,
    info,
    manifest_record: manifest.DbRecord | None = None,
) -> dict[str, object]:
    row: dict[str, object] = {
        "name": name,
        "default": name is None,
        "path": str(path),
    }
    if info is not None:
        row["model"] = info.model
        row["dimension"] = info.dimension
        row["entry_count"] = info.entry_count
    if manifest_record is not None:
        row["created_at"] = manifest_record.created_at
        if manifest_record.description is not None:
            row["description"] = manifest_record.description
    return row


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


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
    keyword_text: Annotated[
        str | None,
        typer.Option("--keyword-text", help="Indexed text for FTS5 keyword search."),
    ] = None,
    semantic_text: Annotated[
        str | None,
        typer.Option("--semantic-text", help="Text to embed for vector search."),
    ] = None,
    context: Annotated[
        str | None,
        typer.Option("--context", help="Free-form context stored alongside the entry."),
    ] = None,
) -> None:
    settings: Settings = ctx.obj
    payload_obj = json.loads(payload) if payload is not None else None
    entry = Entry(
        id=None,
        group_key=group_key,
        group_ref=group_ref,
        payload=payload_obj,
        context=context,
        keyword_text=keyword_text,
        semantic_text=semantic_text,
    )
    with open_db(settings.mount, settings.db) as g:
        [saved] = g.add([entry])
    typer.echo(json.dumps(_entry_to_dict(saved)))


@entry_app.command("update")
@_catches
def entry_update(
    ctx: typer.Context,
    id: Annotated[str, typer.Argument(help="Entry ID.")],
    payload: Annotated[
        str | None, typer.Option("--payload", help="JSON object.")
    ] = None,
    clear_payload: Annotated[bool, typer.Option("--clear-payload")] = False,
    group_ref: Annotated[str | None, typer.Option("--group-ref")] = None,
    clear_group_ref: Annotated[bool, typer.Option("--clear-group-ref")] = False,
    context: Annotated[str | None, typer.Option("--context")] = None,
    clear_context: Annotated[bool, typer.Option("--clear-context")] = False,
) -> None:
    _reject_double(payload, clear_payload, "payload")
    _reject_double(group_ref, clear_group_ref, "group-ref")
    _reject_double(context, clear_context, "context")

    settings: Settings = ctx.obj
    with open_db(settings.mount, settings.db) as g:
        [current] = g.fetch(Filters(id=[id])) or [None]
        if current is None:
            raise GrimoireError(f"No entry {id!r}.")
        patched = Entry(
            id=current.id,
            group_key=current.group_key,
            group_ref=_patch(current.group_ref, group_ref, clear_group_ref),
            payload=_patch_json(current.payload, payload, clear_payload),
            context=_patch(current.context, context, clear_context),
            keyword_text=current.keyword_text,
            semantic_text=current.semantic_text,
            threshold_rank=current.threshold_rank,
            threshold_distance=current.threshold_distance,
        )
        [updated] = g.update([patched])
    typer.echo(json.dumps(_entry_to_dict(updated)))


@entry_app.command("delete")
@_catches
def entry_delete(
    ctx: typer.Context,
    ids: Annotated[list[str], typer.Argument(help="One or more entry IDs.")],
) -> None:
    settings: Settings = ctx.obj
    with open_db(settings.mount, settings.db) as g:
        removed = g.remove(ids)
    for r in removed:
        typer.echo(r)


def _reject_double(value, clear: bool, name: str) -> None:
    if value is not None and clear:
        raise GrimoireError(f"--{name} and --clear-{name} are mutually exclusive.")


def _patch(current, new, clear: bool):
    if clear:
        return None
    if new is not None:
        return new
    return current


def _patch_json(current, raw: str | None, clear: bool):
    if clear:
        return None
    if raw is not None:
        return json.loads(raw)
    return current


_JSONL_FIELDS = (
    "group_key",
    "group_ref",
    "payload",
    "context",
    "keyword_text",
    "semantic_text",
    "threshold_rank",
    "threshold_distance",
)


def _entry_to_jsonl(e: Entry) -> dict:
    out: dict[str, object] = {"id": e.id}
    for field in _JSONL_FIELDS:
        value = getattr(e, field)
        if value is not None:
            out[field] = value
    return out


def _jsonl_to_entry(obj: dict) -> Entry:
    """Hydrate an Entry from a JSONL record. `id` is dropped — it is
    reassigned on import."""
    return Entry(
        id=None,
        group_key=obj.get("group_key"),
        group_ref=obj.get("group_ref"),
        payload=obj.get("payload"),
        context=obj.get("context"),
        keyword_text=obj.get("keyword_text"),
        semantic_text=obj.get("semantic_text"),
        threshold_rank=obj.get("threshold_rank"),
        threshold_distance=obj.get("threshold_distance"),
    )


@app.command("export")
@_catches
def export_cmd(
    ctx: typer.Context,
    output: Annotated[
        Path | None,
        typer.Option(
            "-o",
            "--output",
            help="Write JSONL here. Default: stdout. Use - to force stdout.",
        ),
    ] = None,
    force: Annotated[
        bool,
        typer.Option("--force", help="Overwrite an existing output file."),
    ] = False,
) -> None:
    settings: Settings = ctx.obj
    with open_db(settings.mount, settings.db) as g:
        entries = g.fetch()
    entries.sort(key=lambda e: e.id or "")
    lines = [json.dumps(_entry_to_jsonl(e)) for e in entries]

    if output is None or str(output) == "-":
        for line in lines:
            typer.echo(line)
        return

    if output.exists() and not force:
        raise GrimoireError(f"{output} exists. Pass --force to overwrite.")
    output.write_text(("\n".join(lines) + "\n") if lines else "", encoding="utf-8")
    typer.echo(f"Exported {len(entries)} entries to {output}", err=True)


@app.command("import")
@_catches
def import_cmd(
    ctx: typer.Context,
    source: Annotated[Path, typer.Argument(help="JSONL file to import.")],
) -> None:
    if not source.exists():
        raise GrimoireError(f"No file at {source}")
    entries: list[Entry] = []
    for line_no, raw in enumerate(
        source.read_text(encoding="utf-8").splitlines(), start=1
    ):
        if not raw.strip():
            continue
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise GrimoireError(f"Line {line_no}: {exc.msg}") from exc
        entries.append(_jsonl_to_entry(obj))

    settings: Settings = ctx.obj
    with open_db(settings.mount, settings.db) as g:
        _reject_collisions(g, entries)
        saved = g.add(entries)
    typer.echo(f"Imported {len(saved)} entries", err=True)


def _reject_collisions(g, new: list[Entry]) -> None:
    """Refuse the import if any (group_key, group_ref) collision exists,
    either within the batch or against entries already in the DB."""
    incoming = [(e.group_key, e.group_ref) for e in new if e.group_ref is not None]
    seen: set[tuple[str | None, str]] = set()
    dupes: set[tuple[str | None, str]] = set()
    for pair in incoming:
        if pair in seen:
            dupes.add(pair)
        seen.add(pair)
    if dupes:
        sample = sorted(repr(d) for d in dupes)[:3]
        raise GrimoireError(
            f"Import has duplicate (group_key, group_ref): {', '.join(sample)}"
        )

    if not incoming:
        return
    existing = {
        (e.group_key, e.group_ref) for e in g.fetch() if e.group_ref is not None
    }
    clashes = sorted({pair for pair in incoming if pair in existing})
    if clashes:
        sample = [repr(c) for c in clashes[:3]]
        raise GrimoireError(
            f"(group_key, group_ref) already present: {', '.join(sample)}"
        )


@entry_app.command("get")
@_catches
def entry_get(
    ctx: typer.Context,
    id: Annotated[str, typer.Argument(help="Entry ID.")],
) -> None:
    settings: Settings = ctx.obj
    with open_db(settings.mount, settings.db) as g:
        rows = g.fetch(Filters(id=[id]))
    if not rows:
        raise GrimoireError(f"No entry {id!r}.")
    typer.echo(json.dumps(_entry_to_dict(rows[0])))


@app.command("query")
@_catches
def query(
    ctx: typer.Context,
    group_key: Annotated[
        list[str] | None,
        typer.Option("--group-key", help="Filter to these group_keys (repeatable)."),
    ] = None,
    group_ref: Annotated[
        list[str] | None,
        typer.Option("--group-ref", help="Filter to these group_refs (repeatable)."),
    ] = None,
    cursor: Annotated[
        str | None,
        typer.Option("--cursor", help="Resume after this entry id."),
    ] = None,
    limit: Annotated[
        int,
        typer.Option("--limit", help="Maximum rows to emit."),
    ] = 50,
) -> None:
    settings: Settings = ctx.obj
    filters = Filters(group_key=group_key, group_ref=group_ref)
    with open_db(settings.mount, settings.db) as g:
        rows = g.fetch(filters)
    rows.sort(key=lambda e: e.id or "")
    if cursor is not None:
        rows = [e for e in rows if (e.id or "") > cursor]
    for e in rows[:limit]:
        typer.echo(json.dumps(_entry_to_dict(e)))


@app.command("search")
@_catches
def search(
    ctx: typer.Context,
    query_text: Annotated[str, typer.Argument(help="Query string.")],
    mode: Annotated[
        SearchMode, typer.Option("--mode", help="Index to query.")
    ] = SearchMode.vector,
    group_key: Annotated[
        str | None,
        typer.Option(
            "--group-key",
            help=(
                "Vector: partition to search (default NULL). "
                "Keyword: optional filter on group_key."
            ),
        ),
    ] = None,
    k: Annotated[
        int,
        typer.Option("-k", "--limit", help="Maximum hits to return."),
    ] = 10,
) -> None:
    settings: Settings = ctx.obj
    with open_db(settings.mount, settings.db) as g:
        if mode is SearchMode.vector:
            hits = g.semantic_search(query_text, group_key=group_key, limit=k)
            for h in hits:
                typer.echo(
                    json.dumps({**_entry_to_dict(h.entry), "distance": h.distance})
                )
        else:
            filters = Filters(group_key=[group_key] if group_key else None)
            keyword_hits = g.keyword_search(query_text, filters=filters, limit=k)
            for h in keyword_hits:
                typer.echo(json.dumps({**_entry_to_dict(h.entry), "score": h.score}))


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
