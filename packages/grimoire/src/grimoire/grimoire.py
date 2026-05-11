import sqlite3
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
from grimoire.errors import GrimoireMismatch


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

    def fetch(self, filters: Filters | None = None) -> list[Entry]:
        return entry.fetch(self._conn, filters)

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
