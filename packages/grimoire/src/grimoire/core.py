import json
import sqlite3
import struct
from pathlib import Path
from typing import Any, Self

import sqlite_vec
from ulid import ULID

from grimoire.embedder import Embedder
from grimoire.models import Entry, Stats
from grimoire.schema import bootstrap


class Grimoire:
    """A semantically-indexed datastore backed by one SQLite file."""

    def __init__(self, *, conn: sqlite3.Connection, embedder: Embedder) -> None:
        self._conn = conn
        self._embedder = embedder

    @classmethod
    def open(cls, path: str | Path, *, embedder: Embedder) -> Self:
        conn = _open_conn(str(path))
        try:
            bootstrap(conn, embedder)
            return cls(conn=conn, embedder=embedder)
        except BaseException:
            conn.close()
            raise

    @classmethod
    def peek(cls, path: str | Path) -> Stats | None:
        """Read metadata and counts from a grimoire file without opening it for use.

        Returns None if the file does not exist or is not a grimoire database.
        Does not load sqlite-vec or require an embedder, so it is safe for
        inspection (CLI `info`, model auto-detect) before deciding how to open.
        """
        path = Path(path)
        if not path.exists():
            return None
        try:
            conn = sqlite3.connect(path)
            try:
                row = conn.execute(
                    "SELECT model, dimension FROM grimoire WHERE id = 1"
                ).fetchone()
                if row is None:
                    return None
                version = conn.execute("PRAGMA user_version").fetchone()[0]
                count = conn.execute("SELECT COUNT(*) FROM entries").fetchone()[0]
                kind_rows = conn.execute(
                    "SELECT kind, COUNT(*) FROM entries GROUP BY kind ORDER BY kind"
                ).fetchall()
            finally:
                conn.close()
        except sqlite3.Error:
            return None
        return Stats(
            model=row[0],
            dimension=row[1],
            schema_version=version,
            entry_count=count,
            kinds=dict(kind_rows),
        )

    def add(
        self,
        *,
        kind: str,
        content: str,
        payload: dict[str, Any] | None = None,
        threshold: float | None = None,
    ) -> Entry:
        entry_id = str(ULID())
        vector = self._embedder.embed(content)
        payload_json = json.dumps(payload) if payload is not None else None

        with self._conn:
            self._conn.execute(
                """
                INSERT INTO entries (id, kind, content, payload, threshold)
                VALUES (?, ?, ?, ?, ?)
                """,
                (entry_id, kind, content, payload_json, threshold),
            )
            self._conn.execute(
                "INSERT INTO vectors (entry_id, kind, embedding) VALUES (?, ?, ?)",
                (entry_id, kind, _pack(vector)),
            )

        return Entry(
            id=entry_id,
            kind=kind,
            content=content,
            payload=payload_json,
            threshold=threshold,
        )

    def get(self, entry_id: str) -> Entry | None:
        row = self._conn.execute(
            "SELECT id, kind, content, payload, threshold FROM entries WHERE id = ?",
            (entry_id,),
        ).fetchone()
        return _row_to_entry(row) if row is not None else None

    def list(
        self,
        *,
        kind: str | None = None,
        limit: int = 100,
        after_id: str | None = None,
    ) -> list[Entry]:
        sql = "SELECT id, kind, content, payload, threshold FROM entries"
        params: list[Any] = []
        clauses: list[str] = []
        if kind is not None:
            clauses.append("kind = ?")
            params.append(kind)
        if after_id is not None:
            clauses.append("id > ?")
            params.append(after_id)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY id LIMIT ?"
        params.append(limit)

        rows = self._conn.execute(sql, params).fetchall()
        return [_row_to_entry(r) for r in rows]

    def delete(self, entry_id: str) -> bool:
        with self._conn:
            cursor = self._conn.execute("DELETE FROM entries WHERE id = ?", (entry_id,))
            if cursor.rowcount == 0:
                return False
            self._conn.execute("DELETE FROM vectors WHERE entry_id = ?", (entry_id,))
        return True

    def search(
        self,
        query: str,
        *,
        kind: str | None = None,
        k: int = 10,
        dynamic_threshold: bool = False,
    ) -> list[Entry]:
        vector = self._embedder.embed(query)

        sql = (
            "SELECT e.id, e.kind, e.content, e.payload, e.threshold, v.distance "
            "FROM vectors v JOIN entries e ON e.id = v.entry_id "
            "WHERE v.embedding MATCH ? AND k = ?"
        )
        params: list[Any] = [_pack(vector), k]
        if kind is not None:
            sql += " AND v.kind = ?"
            params.append(kind)
        sql += " ORDER BY v.distance"

        rows = self._conn.execute(sql, params).fetchall()
        results = [
            Entry(
                id=r[0],
                kind=r[1],
                content=r[2],
                payload=r[3],
                threshold=r[4],
                distance=r[5],
            )
            for r in rows
        ]
        if dynamic_threshold:
            results = [
                r for r in results if r.threshold is None or r.distance <= r.threshold
            ]
        return results

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def _open_conn(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _pack(vector: list[float]) -> bytes:
    return struct.pack(f"{len(vector)}f", *vector)


def _row_to_entry(row: tuple) -> Entry:
    return Entry(
        id=row[0],
        kind=row[1],
        content=row[2],
        payload=row[3],
        threshold=row[4],
    )
