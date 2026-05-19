import contextlib
import json
import re
from dataclasses import asdict
from importlib.metadata import version
from pathlib import Path
from typing import Annotated, Any

import typer
from grimoire.data.entry import Entry, Filters
from grimoire.grimoire import Grimoire

from grimoire_cli import embed, mount, telemetry

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


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------


def _existing_mount(ctx: typer.Context) -> mount.Mount:
    mnt: mount.Mount = ctx.obj
    if not mnt.exists():
        raise typer.BadParameter(
            "Mount does not exist; run `grimoire mount create` first."
        )
    return mnt


def _resolve_db(mnt: mount.Mount, db: str | None) -> Path:
    try:
        db_path = mnt.db_path(db)
    except ValueError as e:
        raise typer.BadParameter(str(e)) from e
    if not db_path.exists():
        target = f"database {db!r}" if db else "default database"
        raise typer.BadParameter(f"No {target} in the mount.")
    return db_path


def _open(mnt: mount.Mount, db: str | None) -> Grimoire:
    return Grimoire.open(
        _resolve_db(mnt, db),
        embedder=embed.build_embedder(mnt.models_dir),
        telemetry=telemetry.build_telemetry(),
    )


def _parse_json(label: str, value: str | None) -> Any:
    if value is None:
        return None
    try:
        return json.loads(value)
    except json.JSONDecodeError as e:
        raise typer.BadParameter(f"Invalid JSON for --{label}: {e.msg}") from e


def _parse_kv_list(label: str, items: list[str]) -> dict[str, list[str]]:
    """Parse repeatable KEY=VALUE flags into a dict of value lists."""
    out: dict[str, list[str]] = {}
    for item in items:
        if "=" not in item:
            raise typer.BadParameter(f"--{label} expects KEY=VALUE; got {item!r}")
        k, v = item.split("=", 1)
        out.setdefault(k, []).append(v)
    return out


def _coerce_value(s: str) -> Any:
    """Try int → float → string. Lets a typeless `ordinal_*` column take
    any scalar a caller types on the CLI without forcing them to quote.
    """
    try:
        return int(s)
    except ValueError:
        pass
    try:
        return float(s)
    except ValueError:
        pass
    return s


def _parse_kv_value(label: str, items: list[str]) -> dict[str, Any]:
    """Parse repeatable KEY=VALUE flags, coercing values via `_coerce_value`."""
    out: dict[str, Any] = {}
    for item in items:
        if "=" not in item:
            raise typer.BadParameter(f"--{label} expects KEY=VALUE; got {item!r}")
        k, v = item.split("=", 1)
        out[k] = _coerce_value(v)
    return out


def _build_filters(equals: list[str], gte: list[str], lte: list[str]) -> Filters | None:
    if not (equals or gte or lte):
        return None
    return Filters(
        equals=_parse_kv_list("equals", equals) or None,
        gte=_parse_kv_value("gte", gte) or None,
        lte=_parse_kv_value("lte", lte) or None,
    )


def _index_kwargs(
    ref: str | None,
    ord_1: str | None,
    ord_2: str | None,
    ord_3: str | None,
    ord_4: str | None,
    ord_5: str | None,
    match: str | None,
    search: str | None,
) -> dict[str, Any]:
    """Assemble the kwargs that get forwarded to `g.index(...)`.

    Only includes kwargs the user passed; an entirely empty dict means
    `index()` is a no-op and we can skip calling it.
    """
    kwargs: dict[str, Any] = {}
    if ref is not None:
        kwargs["ref"] = ref
    ords = (ord_1, ord_2, ord_3, ord_4, ord_5)
    if any(o is not None for o in ords):
        kwargs["ord"] = tuple(_coerce_value(o) if o is not None else None for o in ords)
    if match is not None:
        kwargs["match"] = match
    if search is not None:
        kwargs["search"] = search
    return kwargs


def _human_size(n: int) -> str:
    size = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024:
            return f"{int(size)} {unit}" if size == int(size) else f"{size:.1f} {unit}"
        size /= 1024
    return f"{int(size)} TB" if size == int(size) else f"{size:.1f} TB"


def _tokenize_fts(query: str) -> str:
    """Quote each word token and OR-join — defangs FTS5 operators/punctuation."""
    return " OR ".join(f'"{t}"' for t in re.findall(r"\w+", query))


# ----------------------------------------------------------------------
# Mount admin
# ----------------------------------------------------------------------


@mount_app.command(name="create")
def mount_create_cmd(ctx: typer.Context) -> None:
    """Create the mount + default DB. Idempotent."""
    mnt: mount.Mount = ctx.obj
    mount.create(mnt)

    with Grimoire.open(
        mnt.default_db,
        embedder=embed.build_embedder(mnt.models_dir),
        telemetry=telemetry.build_telemetry(),
    ):
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
    db: Annotated[str, typer.Argument(help="Name of the database to add.")],
) -> None:
    """Add a named grimoire database to the mount."""
    mnt = _existing_mount(ctx)

    try:
        db_path = mnt.db_path(db)
    except ValueError as e:
        raise typer.BadParameter(str(e)) from e
    db_path.parent.mkdir(exist_ok=True)

    with Grimoire.open(
        db_path,
        embedder=embed.build_embedder(mnt.models_dir),
        telemetry=telemetry.build_telemetry(),
    ):
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
    db: Annotated[str, typer.Argument(help="Name of the database to remove.")],
    yes: Annotated[bool, typer.Option("--yes", help="Confirm deletion.")] = False,
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


# ----------------------------------------------------------------------
# Entry CRUD
# ----------------------------------------------------------------------


_DB_OPT = typer.Option("--db", "-d", help="Database name (default DB if omitted).")
_REF_OPT = typer.Option("--ref", help="entry_idx.uniq_ref value.")
_ORD_HELP = (
    "entry_idx.ordinal_{n} value. Coerced int → float → string, so a literal"
    " number stores as a number and anything else stores as text."
)
_ORD_1_OPT = typer.Option("--ord-1", help=_ORD_HELP.format(n=1))
_ORD_2_OPT = typer.Option("--ord-2", help=_ORD_HELP.format(n=2))
_ORD_3_OPT = typer.Option("--ord-3", help=_ORD_HELP.format(n=3))
_ORD_4_OPT = typer.Option("--ord-4", help=_ORD_HELP.format(n=4))
_ORD_5_OPT = typer.Option("--ord-5", help=_ORD_HELP.format(n=5))
_MATCH_OPT = typer.Option(
    "--match", help="Text to index in FTS5 (entry_fts). PUT-replaces."
)
_SEARCH_OPT = typer.Option(
    "--search", help="Text to embed into entry_vec. PUT-replaces."
)


@entry_app.command(name="add")
def entry_add_cmd(
    ctx: typer.Context,
    db: Annotated[str | None, _DB_OPT] = None,
    data: Annotated[
        str | None,
        typer.Option("--data", help="JSON for the entry's data column."),
    ] = None,
    ref: Annotated[str | None, _REF_OPT] = None,
    ord_1: Annotated[str | None, _ORD_1_OPT] = None,
    ord_2: Annotated[str | None, _ORD_2_OPT] = None,
    ord_3: Annotated[str | None, _ORD_3_OPT] = None,
    ord_4: Annotated[str | None, _ORD_4_OPT] = None,
    ord_5: Annotated[str | None, _ORD_5_OPT] = None,
    match: Annotated[str | None, _MATCH_OPT] = None,
    search: Annotated[str | None, _SEARCH_OPT] = None,
) -> None:
    """Create a grimoire entry and (optionally) PUT-index its sidecars.

    `--data` writes the entry's JSON blob. The remaining flags are
    forwarded to `index()`; supplying any of `--ref`, `--ord-*`
    PUT-replaces the entry_idx row (omitted columns become NULL).
    """
    mnt = _existing_mount(ctx)
    data_value = _parse_json("data", data)
    idx_kwargs = _index_kwargs(ref, ord_1, ord_2, ord_3, ord_4, ord_5, match, search)

    with _open(mnt, db) as g:
        [created] = g.add([Entry(uniq_id=None, data=data_value)])
        if idx_kwargs:
            try:
                g.index(created.uniq_id, **idx_kwargs)
            except ValueError as e:
                raise typer.BadParameter(str(e)) from e

    typer.echo(json.dumps(asdict(created), indent=2, default=str))


@entry_app.command(name="update")
def entry_update_cmd(
    ctx: typer.Context,
    uniq_id: Annotated[str, typer.Argument(help="uniq_id of the entry to update.")],
    db: Annotated[str | None, _DB_OPT] = None,
    data: Annotated[
        str | None,
        typer.Option("--data", help="Replace the entry's `data` JSON blob."),
    ] = None,
    ref: Annotated[str | None, _REF_OPT] = None,
    ord_1: Annotated[str | None, _ORD_1_OPT] = None,
    ord_2: Annotated[str | None, _ORD_2_OPT] = None,
    ord_3: Annotated[str | None, _ORD_3_OPT] = None,
    ord_4: Annotated[str | None, _ORD_4_OPT] = None,
    ord_5: Annotated[str | None, _ORD_5_OPT] = None,
    match: Annotated[str | None, _MATCH_OPT] = None,
    search: Annotated[str | None, _SEARCH_OPT] = None,
) -> None:
    """Update an entry's data and/or PUT-index its sidecars.

    Omit `--data` to leave the data column untouched. Idx flags follow the
    same PUT semantics as `entry add` — supplying any of `--ref`, `--ord-*`
    wholesale-replaces the entry_idx row.
    """
    mnt = _existing_mount(ctx)
    idx_kwargs = _index_kwargs(ref, ord_1, ord_2, ord_3, ord_4, ord_5, match, search)

    with _open(mnt, db) as g:
        if data is not None:
            data_value = _parse_json("data", data)
            updated = g.update([Entry(uniq_id=uniq_id, data=data_value)])
            if not updated:
                raise typer.BadParameter(f"No entry with uniq_id {uniq_id!r}.")
            current = updated[0]
        else:
            existing = g.get([uniq_id])
            if not existing:
                raise typer.BadParameter(f"No entry with uniq_id {uniq_id!r}.")
            current = existing[0]

        if idx_kwargs:
            try:
                g.index(uniq_id, **idx_kwargs)
            except ValueError as e:
                raise typer.BadParameter(str(e)) from e

    typer.echo(json.dumps(asdict(current), indent=2, default=str))


@entry_app.command(name="get")
def entry_get_cmd(
    ctx: typer.Context,
    uniq_ids: Annotated[
        list[str],
        typer.Argument(help="One or more uniq_ids to fetch."),
    ],
    db: Annotated[str | None, _DB_OPT] = None,
) -> None:
    """Fetch entries by uniq_id."""
    mnt = _existing_mount(ctx)
    with _open(mnt, db) as g:
        entries = g.get(uniq_ids)
    typer.echo(json.dumps([asdict(e) for e in entries], indent=2, default=str))


@entry_app.command(name="remove")
def entry_remove_cmd(
    ctx: typer.Context,
    uniq_id: Annotated[str, typer.Argument(help="uniq_id of the entry to remove.")],
    db: Annotated[str | None, _DB_OPT] = None,
    yes: Annotated[bool, typer.Option("--yes", help="Confirm removal.")] = False,
) -> None:
    """Remove an entry by uniq_id. Sidecar rows are cleaned by DB trigger."""
    if not yes:
        raise typer.BadParameter("Pass --yes to confirm removal.")

    mnt = _existing_mount(ctx)
    with _open(mnt, db) as g:
        removed = g.remove([uniq_id])
    typer.echo(json.dumps({"uniq_id": uniq_id, "removed": bool(removed)}, indent=2))


# ----------------------------------------------------------------------
# Inspection + read commands
# ----------------------------------------------------------------------


@app.command(name="info")
def info_cmd(
    ctx: typer.Context,
    db: Annotated[str | None, _DB_OPT] = None,
) -> None:
    """Show metadata for a grimoire database — lock, schema, per-table counts."""
    mnt = _existing_mount(ctx)
    db_path = _resolve_db(mnt, db)

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


@app.command(name="analyze")
def analyze_cmd(
    ctx: typer.Context,
    db: Annotated[str | None, _DB_OPT] = None,
) -> None:
    """Re-seed the SQLite planner stats by running ANALYZE.

    Run after bulk loads or when the data distribution shifts; the
    rotation composite indexes on `entry_idx` rely on these stats to
    pick the right index for multi-ordinal predicates.
    """
    mnt = _existing_mount(ctx)
    with _open(mnt, db) as g:
        g.analyze()
    typer.echo(json.dumps({"db": db, "analyzed": True}, indent=2))


_EQUALS_OPT = typer.Option(
    "--equals",
    help=(
        "Filter `entry_idx.<col> IN (...)`. Repeatable as KEY=VALUE "
        "(e.g. --equals ordinal_4=note)."
    ),
)
_GTE_OPT = typer.Option(
    "--gte",
    help=(
        "Filter `ordinal_N >= value`. Repeatable as KEY=VALUE."
        " Values are coerced int → float → string."
    ),
)
_LTE_OPT = typer.Option(
    "--lte",
    help=(
        "Filter `ordinal_N <= value`. Repeatable as KEY=VALUE."
        " Values are coerced int → float → string."
    ),
)


def _format_entry_pair_list(entries: list, second: list, key: str) -> str:
    """Zip parallel lists into `[{entry, <key>}, ...]` JSON."""
    if key == "index":
        return json.dumps(
            [
                {"entry": asdict(e), "index": asdict(i)}
                for e, i in zip(entries, second, strict=True)
            ],
            indent=2,
            default=str,
        )
    # hits: pull just the score/distance, drop the redundant uniq_id
    return json.dumps(
        [
            {"entry": asdict(e), key: getattr(h, key)}
            for e, h in zip(entries, second, strict=True)
        ],
        indent=2,
        default=str,
    )


@app.command(name="query")
def query_cmd(
    ctx: typer.Context,
    db: Annotated[str | None, _DB_OPT] = None,
    equals: Annotated[list[str] | None, _EQUALS_OPT] = None,
    gte: Annotated[list[str] | None, _GTE_OPT] = None,
    lte: Annotated[list[str] | None, _LTE_OPT] = None,
    cursor: Annotated[
        str | None,
        typer.Option(
            "--cursor",
            help="Return rows with uniq_id > this. Pass the last id of the prior page.",
        ),
    ] = None,
    limit: Annotated[
        int,
        typer.Option("--limit", help="Max rows to return.", min=0),
    ] = 100,
) -> None:
    """Browse entry_idx rows, optionally filtered. Joins entry for the data side."""
    mnt = _existing_mount(ctx)
    try:
        filters = _build_filters(equals or [], gte or [], lte or [])
    except typer.BadParameter:
        raise
    with _open(mnt, db) as g:
        try:
            entries, indexes = g.query(filters, limit=limit, cursor=cursor)
        except ValueError as e:
            raise typer.BadParameter(str(e)) from e

    typer.echo(_format_entry_pair_list(entries, indexes, "index"))


@app.command(name="fetch")
def fetch_cmd(
    ctx: typer.Context,
    uniq_refs: Annotated[
        list[str],
        typer.Argument(help="One or more uniq_ref values to look up."),
    ],
    db: Annotated[str | None, _DB_OPT] = None,
) -> None:
    """Fetch entries by uniq_ref (via entry_idx JOIN)."""
    mnt = _existing_mount(ctx)
    with _open(mnt, db) as g:
        entries, indexes = g.fetch(uniq_refs)
    typer.echo(_format_entry_pair_list(entries, indexes, "index"))


@app.command(name="match")
def match_cmd(
    ctx: typer.Context,
    query: Annotated[str, typer.Argument(help="Search query — tokenized for FTS5.")],
    db: Annotated[str | None, _DB_OPT] = None,
    equals: Annotated[list[str] | None, _EQUALS_OPT] = None,
    gte: Annotated[list[str] | None, _GTE_OPT] = None,
    lte: Annotated[list[str] | None, _LTE_OPT] = None,
    limit: Annotated[int, typer.Option("--limit", help="Max hits.", min=0)] = 10,
) -> None:
    """FTS5 BM25 keyword search. Filters apply via JOIN to entry_idx.

    `score` is positive (higher = better); query is tokenized so apostrophes,
    punctuation, and bareword FTS5 operators (AND/OR/NOT/NEAR/*) can't
    reach the parser.
    """
    mnt = _existing_mount(ctx)
    fts_query = _tokenize_fts(query)
    if not fts_query:
        typer.echo("[]")
        return

    try:
        filters = _build_filters(equals or [], gte or [], lte or [])
    except typer.BadParameter:
        raise
    with _open(mnt, db) as g:
        try:
            entries, hits = g.match(fts_query, filters=filters, limit=limit)
        except ValueError as e:
            raise typer.BadParameter(str(e)) from e

    typer.echo(_format_entry_pair_list(entries, hits, "score"))


@app.command(name="search")
def search_cmd(
    ctx: typer.Context,
    query: Annotated[str, typer.Argument(help="Search query — embedded for vec0 KNN.")],
    db: Annotated[str | None, _DB_OPT] = None,
    limit: Annotated[int, typer.Option("--limit", help="Max hits.", min=0)] = 10,
) -> None:
    """vec0 KNN semantic search. `distance` is raw (lower = better, non-negative)."""
    mnt = _existing_mount(ctx)
    with _open(mnt, db) as g:
        entries, hits = g.search(query, limit=limit)
    typer.echo(_format_entry_pair_list(entries, hits, "distance"))


# ----------------------------------------------------------------------
# MCP server
# ----------------------------------------------------------------------


@mcp_app.command(name="serve")
def mcp_serve_cmd(ctx: typer.Context) -> None:
    """Run the grimoire MCP server over stdio against this mount."""
    from grimoire_cli import mcp as mcp_module

    mnt = _existing_mount(ctx)
    mcp_module.build_server(mnt).run()
