import sqlite3
from dataclasses import dataclass
from pathlib import Path

import sqlite_vec

from grimoire.data import entry, meta, schema
from grimoire.data.entry import (
    Entry,
    Filters,
    KeywordHit,
    SemanticHit,
    _IndexedEntry,
)
from grimoire.embed import Embedder
from grimoire.errors import GrimoireMismatch, GrimoireNotFound


@dataclass(frozen=True, slots=True)
class Peek:
    model: str
    dimension: int
    schema_version: int
    entry_count: int
    group_counts: dict[str | None, int]


def _index(entries: list[Entry], embedder: Embedder) -> list[_IndexedEntry]:
    texts = [entry.semantic_text for entry in entries]
    to_embed = [text for text in texts if text is not None]
    vec_iter = iter(embedder.embed_many(to_embed)) if to_embed else iter(())
    return [
        _IndexedEntry(entry, next(vec_iter) if text is not None else None)
        for entry, text in zip(entries, texts, strict=True)
    ]


class Grimoire:
    def __init__(self, conn: sqlite3.Connection, embedder: Embedder) -> None:
        self._conn = conn
        self.embedder = embedder

    def __enter__(self) -> "Grimoire":
        return self

    def __exit__(self, exc_type, *_) -> None:
        if exc_type is None:
            self._conn.commit()
        else:
            self._conn.rollback()

    def add(self, entries: list[Entry]) -> list[Entry]:
        return entry.add(self._conn, _index(entries, self.embedder))

    def update(self, entries: list[Entry]) -> list[Entry]:
        return entry.update(self._conn, entries)

    def remove(self, ids: list[str]) -> list[str]:
        return entry.remove(self._conn, ids)

    def fetch(
        self,
        filters: Filters | None = None,
        limit: int = 100,
    ) -> list[Entry]:
        return entry.fetch(self._conn, filters, limit)

    def keyword_search(
        self,
        query: str,
        filters: Filters | None = None,
        limit: int | None = None,
    ) -> list[KeywordHit]:
        return entry.keyword_search(self._conn, query, filters, limit)

    def semantic_search(
        self,
        query: str,
        group_key: str | None,
        limit: int = 10,
    ) -> list[SemanticHit]:
        return entry.semantic_search(
            self._conn,
            self.embedder.embed(query),
            group_key,
            limit,
        )


def peek(path: str | Path) -> Peek:
    """Inspect a grimoire file without loading an embedder or sqlite-vec.

    Returns model, dimension, schema version, total entry count, and
    per-group counts. Raises `GrimoireNotFound` if the file does not exist
    or has not been initialized.
    """
    p = Path(path)
    if not p.exists():
        raise GrimoireNotFound(f"No grimoire at {p}")

    conn = sqlite3.connect(p)
    conn.row_factory = sqlite3.Row

    try:
        if schema.read_version(conn) == 0:
            raise GrimoireNotFound(f"{p} is not an initialized grimoire")
        
        schema.validate(conn)
        model = meta.fetch(conn, "model")
        dimension_str = meta.fetch(conn, "dimension")

        if model is None or dimension_str is None:
            raise GrimoireNotFound(f"{p} is missing its embedder lock")
        
        entry_count = conn.execute("SELECT COUNT(*) FROM entry").fetchone()[0]
        rows = conn.execute(
            "SELECT group_key, COUNT(*) AS n FROM entry GROUP BY group_key "
            "ORDER BY group_key IS NULL, group_key"
        ).fetchall()

        return Peek(
            model=model,
            dimension=int(dimension_str),
            schema_version=schema.read_version(conn),
            entry_count=entry_count,
            group_counts={r["group_key"]: r["n"] for r in rows},
        )
    finally:
        conn.close()


def open(path: str | Path, *, embedder: Embedder) -> Grimoire:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row

    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)

    if schema.read_version(conn) == 0:
        schema.create(conn, model=embedder.model, dimension=embedder.dimension)
    else:
        schema.validate(conn)
        stored_model = meta.fetch(conn, "model")
        stored_dimension = int(meta.fetch(conn, "dimension"))
        if stored_model != embedder.model or stored_dimension != embedder.dimension:
            raise GrimoireMismatch(
                f"Embedder reports model={embedder.model!r} dimension={embedder.dimension}, "
                f"file locked to model={stored_model!r} dimension={stored_dimension}."
            )

    return Grimoire(conn, embedder=embedder)
