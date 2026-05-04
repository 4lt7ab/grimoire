import json
import sqlite3
from pathlib import Path
from typing import Annotated, NoReturn

import typer
from grimoire import Entry, Grimoire, GrimoireError

RECOGNIZED_FIELDS = {"kind", "content", "payload", "threshold"}
REQUIRED_FIELDS = {"kind", "content"}
PROGRESS_EVERY = 1000
DEFAULT_MODEL = "BAAI/bge-small-en-v1.5"
DEFAULT_DB = Path(".grimoire/data/grimoire.db")
DEFAULT_CACHE = Path(".grimoire/models")

app = typer.Typer(
    name="grimoire",
    no_args_is_help=True,
    pretty_exceptions_enable=False,
)


@app.callback()
def _callback() -> None:
    """Manage a grimoire datastore."""


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
    db: Annotated[
        Path,
        typer.Option(help="Path to the grimoire SQLite file."),
    ] = DEFAULT_DB,
    model: Annotated[
        str,
        typer.Option(
            help=("fastembed model name (only used when creating a new file).")
        ),
    ] = DEFAULT_MODEL,
) -> None:
    """Bulk-ingest records into a grimoire."""
    records = _load_records(file)
    if not records:
        typer.echo(f"No records to ingest from {file}")
        return

    with _open_grimoire(db, model_override=model) as g:
        for i, record in enumerate(records, 1):
            g.add(
                kind=record["kind"],
                content=record["content"],
                payload=record.get("payload"),
                threshold=record.get("threshold"),
            )
            if i % PROGRESS_EVERY == 0:
                typer.echo(f"  ingested {i}...", err=True)

    typer.echo(f"Ingested {len(records)} records into {db}")


@app.command()
def search(
    query: Annotated[str, typer.Argument(help="Query text to embed and search for.")],
    db: Annotated[
        Path, typer.Option(help="Path to the grimoire SQLite file.", exists=True)
    ] = DEFAULT_DB,
    kind: Annotated[
        str | None, typer.Option(help="Restrict results to entries of this kind.")
    ] = None,
    k: Annotated[int, typer.Option(help="Number of results to return.")] = 10,
) -> None:
    """Run a semantic search against a grimoire."""
    with _open_grimoire(db) as g:
        for entry in g.search(query, kind=kind, k=k):
            _print_entry(entry)


@app.command(name="list")
def list_entries(
    db: Annotated[
        Path, typer.Option(help="Path to the grimoire SQLite file.", exists=True)
    ] = DEFAULT_DB,
    kind: Annotated[
        str | None, typer.Option(help="Restrict to entries of this kind.")
    ] = None,
    limit: Annotated[
        int, typer.Option(help="Maximum number of entries to return.")
    ] = 100,
    after_id: Annotated[
        str | None, typer.Option(help="Cursor: return entries with id > this value.")
    ] = None,
) -> None:
    """Paginate entries in chronological order (by id)."""
    with _open_grimoire(db) as g:
        for entry in g.list(kind=kind, limit=limit, after_id=after_id):
            _print_entry(entry)


@app.command()
def get(
    entry_id: Annotated[str, typer.Argument(help="Entry id (ULID).")],
    db: Annotated[
        Path, typer.Option(help="Path to the grimoire SQLite file.", exists=True)
    ] = DEFAULT_DB,
) -> None:
    """Fetch a single entry by id."""
    with _open_grimoire(db) as g:
        entry = g.get(entry_id)
        if entry is None:
            _fail(f"No entry with id {entry_id!r}")
        _print_entry(entry)


@app.command()
def delete(
    entry_id: Annotated[str, typer.Argument(help="Entry id (ULID).")],
    db: Annotated[
        Path, typer.Option(help="Path to the grimoire SQLite file.", exists=True)
    ] = DEFAULT_DB,
) -> None:
    """Delete an entry by id."""
    with _open_grimoire(db) as g:
        if not g.delete(entry_id):
            _fail(f"No entry with id {entry_id!r}")
    typer.echo(f"Deleted {entry_id}")


def _open_grimoire(db: Path, *, model_override: str | None = None) -> Grimoire:
    """Open a Grimoire, auto-detecting the embedding model from the file when possible.

    Resolution order:
      1. Model name stored in the file (if file exists and is initialized).
      2. `model_override` argument (typically a CLI --model flag).
      3. The library default model.
    """
    db.parent.mkdir(parents=True, exist_ok=True)
    DEFAULT_CACHE.mkdir(parents=True, exist_ok=True)
    model_name = _read_stored_model(db) or model_override or DEFAULT_MODEL
    try:
        from grimoire.embedders import FastembedEmbedder

        embedder = FastembedEmbedder(model_name, cache_folder=DEFAULT_CACHE)
    except ImportError as exc:
        _fail(str(exc))
    try:
        return Grimoire.open(db, embedder=embedder)
    except GrimoireError as exc:
        _fail(str(exc))


def _read_stored_model(db: Path) -> str | None:
    if not db.exists():
        return None
    try:
        conn = sqlite3.connect(db)
        try:
            row = conn.execute("SELECT model FROM grimoire WHERE id = 1").fetchone()
        finally:
            conn.close()
    except sqlite3.Error:
        return None
    return row[0] if row else None


def _load_records(path: Path) -> list[dict]:
    records: list[dict] = []
    with path.open() as f:
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


def _print_entry(entry: Entry) -> None:
    record: dict[str, object] = {
        "id": entry.id,
        "kind": entry.kind,
        "content": entry.content,
    }
    if entry.payload is not None:
        try:
            record["payload"] = json.loads(entry.payload)
        except json.JSONDecodeError:
            record["payload"] = entry.payload
    if entry.threshold is not None:
        record["threshold"] = entry.threshold
    if entry.distance is not None:
        record["distance"] = entry.distance
    typer.echo(json.dumps(record))


def _fail(message: str) -> NoReturn:
    typer.echo(f"Error: {message}", err=True)
    raise typer.Exit(code=1)


def main() -> None:
    """Console-script entrypoint for the `grimoire` CLI."""
    app()


if __name__ == "__main__":
    main()
