from __future__ import annotations

import json
import sqlite3
import struct
from collections.abc import Iterable, Mapping
from datetime import datetime
from pathlib import Path
from typing import Any, Self

import sqlite_vec
from ulid import ULID

from grimoire.embedder import Embedder
from grimoire.errors import GrimoireNotFound
from grimoire.models import Entry, Stats
from grimoire.schema import create, validate

_WARMUP_PROBE = " "

# BM25 column weights for keyword_search: (content, keywords).
# Keyword matches outrank content matches by this ratio. Tunable here.
KEYWORD_BM25_WEIGHTS = (1.0, 5.0)
_BM25_RANK = f"bm25(entries_fts, {KEYWORD_BM25_WEIGHTS[0]}, {KEYWORD_BM25_WEIGHTS[1]})"


class Grimoire:
    """A semantically-indexed datastore backed by one SQLite file."""

    def __init__(self, *, conn: sqlite3.Connection, embedder: Embedder) -> None:
        self._conn = conn
        self._embedder = embedder

    @classmethod
    def init(cls, path: str | Path, *, embedder: Embedder) -> Self:
        """Create the grimoire if missing, validate if present, and warm the embedder.

        Idempotent. After this returns, the file exists with a lock row matching
        the supplied embedder, and the embedder has been exercised once via
        `embed(_WARMUP_PROBE)` so any deferred setup work has happened.
        """
        path = Path(path)
        is_new = not path.exists()
        if is_new:
            path.parent.mkdir(parents=True, exist_ok=True)
        conn = _open_conn(str(path))
        try:
            if is_new:
                create(conn, embedder)
            else:
                validate(conn, embedder)
            embedder.embed(_WARMUP_PROBE)
            return cls(conn=conn, embedder=embedder)
        except BaseException:
            conn.close()
            raise

    @classmethod
    def open(cls, path: str | Path, *, embedder: Embedder) -> Self:
        """Open an existing grimoire; raises `GrimoireNotFound` if missing."""
        path = Path(path)
        if not path.exists():
            raise GrimoireNotFound(f"No grimoire at {path}")
        conn = _open_conn(str(path))
        try:
            validate(conn, embedder)
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
        keywords: list[str] | None = None,
    ) -> Entry:
        entry_id = str(ULID())
        vector = self._embedder.embed(content)
        payload_json = json.dumps(payload) if payload is not None else None
        keywords_json = json.dumps(keywords) if keywords is not None else None
        keywords_text = " ".join(keywords) if keywords else ""

        with self._conn:
            self._conn.execute(
                """
                INSERT INTO entries (id, kind, content, keywords, payload, threshold)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (entry_id, kind, content, keywords_json, payload_json, threshold),
            )
            self._conn.execute(
                "INSERT INTO vectors (entry_id, kind, embedding) VALUES (?, ?, ?)",
                (entry_id, kind, _pack(vector)),
            )
            self._conn.execute(
                "INSERT INTO entries_fts (content, keywords, entry_id) "
                "VALUES (?, ?, ?)",
                (content, keywords_text, entry_id),
            )

        return Entry(
            id=entry_id,
            kind=kind,
            content=content,
            payload=payload,
            threshold=threshold,
            keywords=keywords,
        )

    def add_many(self, records: Iterable[Mapping[str, Any]]) -> list[Entry]:
        """Insert many records in one transaction with one batched embed call.

        Each record is a mapping accepting the same keys as `add`'s kwargs:
        `kind` and `content` are required; `payload`, `threshold`, and
        `keywords` are optional. Returns the inserted entries in input order.

        Atomic: if embedding or any insert fails, nothing is committed —
        unlike a loop over `add`, which would leave partial state behind.
        """
        records = list(records)
        if not records:
            return []

        contents = [r["content"] for r in records]
        vectors = self._embedder.embed_many(contents)

        entries: list[Entry] = []
        entries_rows: list[tuple] = []
        vectors_rows: list[tuple] = []
        fts_rows: list[tuple] = []

        for record, vector in zip(records, vectors, strict=True):
            entry_id = str(ULID())
            kind = record["kind"]
            content = record["content"]
            payload = record.get("payload")
            threshold = record.get("threshold")
            keywords = record.get("keywords")

            payload_json = json.dumps(payload) if payload is not None else None
            keywords_json = json.dumps(keywords) if keywords is not None else None
            keywords_text = " ".join(keywords) if keywords else ""

            entries_rows.append(
                (entry_id, kind, content, keywords_json, payload_json, threshold)
            )
            vectors_rows.append((entry_id, kind, _pack(vector)))
            fts_rows.append((content, keywords_text, entry_id))

            entries.append(
                Entry(
                    id=entry_id,
                    kind=kind,
                    content=content,
                    payload=payload,
                    threshold=threshold,
                    keywords=keywords,
                )
            )

        with self._conn:
            self._conn.executemany(
                "INSERT INTO entries (id, kind, content, keywords, payload, threshold) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                entries_rows,
            )
            self._conn.executemany(
                "INSERT INTO vectors (entry_id, kind, embedding) VALUES (?, ?, ?)",
                vectors_rows,
            )
            self._conn.executemany(
                "INSERT INTO entries_fts (content, keywords, entry_id) "
                "VALUES (?, ?, ?)",
                fts_rows,
            )

        return entries

    def get(self, entry_id: str) -> Entry | None:
        row = self._conn.execute(
            "SELECT id, kind, content, keywords, payload, threshold "
            "FROM entries WHERE id = ?",
            (entry_id,),
        ).fetchone()
        return _row_to_entry(row) if row is not None else None

    def list(
        self,
        *,
        kind: str | None = None,
        limit: int = 100,
        after_id: str | None = None,
        created_after: datetime | None = None,
        created_before: datetime | None = None,
    ) -> list[Entry]:
        sql = "SELECT id, kind, content, keywords, payload, threshold FROM entries"
        params: list[Any] = []
        clauses: list[str] = []
        if kind is not None:
            clauses.append("kind = ?")
            params.append(kind)
        if after_id is not None:
            clauses.append("id > ?")
            params.append(after_id)
        if created_after is not None:
            clauses.append("id >= ?")
            params.append(_ulid_floor(created_after))
        if created_before is not None:
            clauses.append("id < ?")
            params.append(_ulid_floor(created_before))
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
            self._conn.execute(
                "DELETE FROM entries_fts WHERE entry_id = ?", (entry_id,)
            )
        return True

    def vector_search(
        self,
        query: str,
        *,
        kind: str | None = None,
        k: int = 10,
        dynamic_threshold: bool = False,
        created_after: datetime | None = None,
        created_before: datetime | None = None,
    ) -> list[Entry]:
        """Return up to `k` entries ranked by vector distance to `query`.

        Filters interact with the KNN in two different ways:

        - `kind` is pushed into the vector index's partition key, so the
          KNN considers only entries of that kind from the start.
        - `created_after`, `created_before`, and `dynamic_threshold` apply
          AFTER the KNN returns its top-k. With a narrow time window or
          tight per-record thresholds, this can return fewer than `k`
          results — even when many qualifying entries exist further down
          the similarity ranking. Raise `k` to compensate.
        """
        vector = self._embedder.embed(query)

        sql = (
            "SELECT e.id, e.kind, e.content, e.keywords, e.payload, e.threshold, "
            "v.distance "
            "FROM vectors v JOIN entries e ON e.id = v.entry_id "
            "WHERE v.embedding MATCH ? AND k = ?"
        )
        params: list[Any] = [_pack(vector), k]
        if kind is not None:
            sql += " AND v.kind = ?"
            params.append(kind)
        if created_after is not None:
            sql += " AND e.id >= ?"
            params.append(_ulid_floor(created_after))
        if created_before is not None:
            sql += " AND e.id < ?"
            params.append(_ulid_floor(created_before))
        sql += " ORDER BY v.distance"

        rows = self._conn.execute(sql, params).fetchall()
        results = [
            Entry(
                id=r[0],
                kind=r[1],
                content=r[2],
                keywords=json.loads(r[3]) if r[3] is not None else None,
                payload=json.loads(r[4]) if r[4] is not None else None,
                threshold=r[5],
                distance=r[6],
            )
            for r in rows
        ]
        if dynamic_threshold:
            results = [
                r for r in results if r.threshold is None or r.distance <= r.threshold
            ]
        return results

    def keyword_search(
        self,
        query: str,
        *,
        kind: str | None = None,
        k: int = 10,
        created_after: datetime | None = None,
        created_before: datetime | None = None,
    ) -> list[Entry]:
        sql = (
            f"SELECT e.id, e.kind, e.content, e.keywords, e.payload, e.threshold, "
            f"{_BM25_RANK} AS rank "
            "FROM entries_fts JOIN entries e ON e.id = entries_fts.entry_id "
            "WHERE entries_fts MATCH ?"
        )
        params: list[Any] = [query]
        if kind is not None:
            sql += " AND e.kind = ?"
            params.append(kind)
        if created_after is not None:
            sql += " AND e.id >= ?"
            params.append(_ulid_floor(created_after))
        if created_before is not None:
            sql += " AND e.id < ?"
            params.append(_ulid_floor(created_before))
        sql += f" ORDER BY {_BM25_RANK} LIMIT ?"
        params.append(k)

        rows = self._conn.execute(sql, params).fetchall()
        return [
            Entry(
                id=r[0],
                kind=r[1],
                content=r[2],
                keywords=json.loads(r[3]) if r[3] is not None else None,
                payload=json.loads(r[4]) if r[4] is not None else None,
                threshold=r[5],
                rank=r[6],
            )
            for r in rows
        ]

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


def _ulid_floor(dt: datetime) -> str:
    return str(ULID.from_datetime(dt))[:10] + "0" * 16


def _row_to_entry(row: tuple) -> Entry:
    return Entry(
        id=row[0],
        kind=row[1],
        content=row[2],
        keywords=json.loads(row[3]) if row[3] is not None else None,
        payload=json.loads(row[4]) if row[4] is not None else None,
        threshold=row[5],
    )
