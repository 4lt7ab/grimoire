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


class _Unset:
    """Sentinel for "field not supplied" in `update()`.

    `None` is a meaningful value for nullable fields (clears the column), so
    we can't reuse it to mean "leave alone". A dedicated sentinel keeps the
    two cases unambiguous.
    """

    _instance: _Unset | None = None

    def __new__(cls) -> _Unset:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __repr__(self) -> str:
        return "UNSET"


_UNSET: _Unset = _Unset()


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

    def update(
        self,
        entry_id: str,
        *,
        kind: str | None = None,
        content: str | None = None,
        payload: dict[str, Any] | None | _Unset = _UNSET,
        threshold: float | None | _Unset = _UNSET,
        keywords: list[str] | None | _Unset = _UNSET,
    ) -> Entry | None:
        """Patch fields on an entry, leaving omitted fields untouched.

        `kind` and `content` are non-nullable — pass a value to change them,
        or omit (default `None`) to leave them alone. `payload`, `threshold`,
        and `keywords` are nullable — omit to leave alone, pass `None` to
        clear, or pass a new value to replace.

        Returns the updated entry, or `None` if the id is unknown. Re-embeds
        only when `content` changed, re-indexes FTS only when `content` or
        `keywords` changed, and rewrites the vector row only when `content`
        or `kind` changed.
        """
        current = self.get(entry_id)
        if current is None:
            return None

        new_kind = current.kind if kind is None else kind
        new_content = current.content if content is None else content
        new_payload = current.payload if isinstance(payload, _Unset) else payload
        new_threshold = (
            current.threshold if isinstance(threshold, _Unset) else threshold
        )
        new_keywords = current.keywords if isinstance(keywords, _Unset) else keywords

        content_changed = new_content != current.content
        keywords_changed = new_keywords != current.keywords
        kind_changed = new_kind != current.kind

        payload_json = json.dumps(new_payload) if new_payload is not None else None
        keywords_json = json.dumps(new_keywords) if new_keywords is not None else None
        keywords_text = " ".join(new_keywords) if new_keywords else ""

        with self._conn:
            self._conn.execute(
                """
                UPDATE entries
                SET kind = ?, content = ?, keywords = ?, payload = ?, threshold = ?
                WHERE id = ?
                """,
                (
                    new_kind,
                    new_content,
                    keywords_json,
                    payload_json,
                    new_threshold,
                    entry_id,
                ),
            )

            if content_changed or kind_changed:
                # vec0 partitions on `kind`, so a kind change requires moving
                # the row to a different partition — done as delete + reinsert.
                # When only kind changed, reuse the stored embedding blob to
                # avoid an unnecessary embedder call.
                if content_changed:
                    new_blob = _pack(self._embedder.embed(new_content))
                else:
                    row = self._conn.execute(
                        "SELECT embedding FROM vectors WHERE entry_id = ?",
                        (entry_id,),
                    ).fetchone()
                    new_blob = row[0]
                self._conn.execute(
                    "DELETE FROM vectors WHERE entry_id = ?", (entry_id,)
                )
                self._conn.execute(
                    "INSERT INTO vectors (entry_id, kind, embedding) VALUES (?, ?, ?)",
                    (entry_id, new_kind, new_blob),
                )

            if content_changed or keywords_changed:
                self._conn.execute(
                    "DELETE FROM entries_fts WHERE entry_id = ?", (entry_id,)
                )
                self._conn.execute(
                    "INSERT INTO entries_fts (content, keywords, entry_id) "
                    "VALUES (?, ?, ?)",
                    (new_content, keywords_text, entry_id),
                )

        return Entry(
            id=entry_id,
            kind=new_kind,
            content=new_content,
            payload=new_payload,
            threshold=new_threshold,
            keywords=new_keywords,
        )

    def update_many(self, records: Iterable[Mapping[str, Any]]) -> list[Entry | None]:
        """Patch many entries in one transaction with one batched embed call.

        Each record must include `id`; remaining keys mirror `update`'s
        kwargs (`kind`, `content`, `payload`, `threshold`, `keywords`).
        Absent keys leave the field unchanged; passing `None` clears nullable
        fields (`payload`, `threshold`, `keywords`).

        Returns one `Entry | None` per input record in input order — `None`
        when the id is unknown. Atomic: if embedding or any update fails,
        nothing is committed — unlike a loop over `update`, which would leave
        partial state behind.

        Duplicate ids in input raise `ValueError`: which record wins is
        ambiguous, and the returned entries would lie about the post-batch
        state. Callers should dedupe upstream.
        """
        records = list(records)
        if not records:
            return []

        ids = [r["id"] for r in records]
        if len(ids) != len(set(ids)):
            raise ValueError("update_many: duplicate ids in input")

        placeholders = ",".join(["?"] * len(ids))
        rows = self._conn.execute(
            f"SELECT id, kind, content, keywords, payload, threshold "
            f"FROM entries WHERE id IN ({placeholders})",
            ids,
        ).fetchall()
        current_by_id = {row[0]: _row_to_entry(row) for row in rows}

        # First pass: compute target state per record, gather contents to embed.
        targets: list[dict[str, Any] | None] = []
        contents_to_embed: list[str] = []
        embed_indices: list[int] = []
        for i, r in enumerate(records):
            current = current_by_id.get(r["id"])
            if current is None:
                targets.append(None)
                continue
            new_kind = r.get("kind", current.kind)
            new_content = r.get("content", current.content)
            new_payload = r.get("payload", current.payload)
            new_threshold = r.get("threshold", current.threshold)
            new_keywords = r.get("keywords", current.keywords)
            target = {
                "id": r["id"],
                "kind": new_kind,
                "content": new_content,
                "payload": new_payload,
                "threshold": new_threshold,
                "keywords": new_keywords,
                "content_changed": new_content != current.content,
                "kind_changed": new_kind != current.kind,
                "keywords_changed": new_keywords != current.keywords,
            }
            targets.append(target)
            if target["content_changed"]:
                embed_indices.append(i)
                contents_to_embed.append(new_content)

        # One batched embed call covering only records whose content changed.
        blobs: dict[int, bytes] = {}
        if contents_to_embed:
            new_vectors = self._embedder.embed_many(contents_to_embed)
            for idx, vec in zip(embed_indices, new_vectors, strict=True):
                blobs[idx] = _pack(vec)

        results: list[Entry | None] = []
        with self._conn:
            for i, t in enumerate(targets):
                if t is None:
                    results.append(None)
                    continue

                payload_json = (
                    json.dumps(t["payload"]) if t["payload"] is not None else None
                )
                keywords_json = (
                    json.dumps(t["keywords"]) if t["keywords"] is not None else None
                )
                keywords_text = " ".join(t["keywords"]) if t["keywords"] else ""

                self._conn.execute(
                    "UPDATE entries SET kind = ?, content = ?, keywords = ?, "
                    "payload = ?, threshold = ? WHERE id = ?",
                    (
                        t["kind"],
                        t["content"],
                        keywords_json,
                        payload_json,
                        t["threshold"],
                        t["id"],
                    ),
                )

                if t["content_changed"] or t["kind_changed"]:
                    if i in blobs:
                        new_blob = blobs[i]
                    else:
                        # Kind changed but content did not — reuse stored embedding.
                        row = self._conn.execute(
                            "SELECT embedding FROM vectors WHERE entry_id = ?",
                            (t["id"],),
                        ).fetchone()
                        new_blob = row[0]
                    self._conn.execute(
                        "DELETE FROM vectors WHERE entry_id = ?", (t["id"],)
                    )
                    self._conn.execute(
                        "INSERT INTO vectors (entry_id, kind, embedding) "
                        "VALUES (?, ?, ?)",
                        (t["id"], t["kind"], new_blob),
                    )

                if t["content_changed"] or t["keywords_changed"]:
                    self._conn.execute(
                        "DELETE FROM entries_fts WHERE entry_id = ?", (t["id"],)
                    )
                    self._conn.execute(
                        "INSERT INTO entries_fts (content, keywords, entry_id) "
                        "VALUES (?, ?, ?)",
                        (t["content"], keywords_text, t["id"]),
                    )

                results.append(
                    Entry(
                        id=t["id"],
                        kind=t["kind"],
                        content=t["content"],
                        payload=t["payload"],
                        threshold=t["threshold"],
                        keywords=t["keywords"],
                    )
                )
        return results

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

    def delete_many(self, ids: Iterable[str]) -> list[bool]:
        """Delete many entries in one transaction.

        Returns one `bool` per input id in input order — `True` if the entry
        existed and was deleted, `False` otherwise. Duplicate ids each
        receive the same answer (their pre-call existence). Atomic: all
        successful deletes apply or none do.
        """
        ids = list(ids)
        if not ids:
            return []

        unique = list(dict.fromkeys(ids))  # preserves first-seen order, deduped
        placeholders = ",".join(["?"] * len(unique))
        existing = {
            row[0]
            for row in self._conn.execute(
                f"SELECT id FROM entries WHERE id IN ({placeholders})", unique
            ).fetchall()
        }

        if existing:
            existing_list = list(existing)
            ph = ",".join(["?"] * len(existing_list))
            with self._conn:
                self._conn.execute(
                    f"DELETE FROM entries WHERE id IN ({ph})", existing_list
                )
                self._conn.execute(
                    f"DELETE FROM vectors WHERE entry_id IN ({ph})", existing_list
                )
                self._conn.execute(
                    f"DELETE FROM entries_fts WHERE entry_id IN ({ph})",
                    existing_list,
                )

        return [eid in existing for eid in ids]

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
