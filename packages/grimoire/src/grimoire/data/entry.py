import json
import sqlite3
import struct
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from ulid import ULID


@dataclass(frozen=True, slots=True)
class Entry:
    id: str | None
    group_key: str | None
    group_ref: str | None
    payload: dict[str, Any] | None
    context: str | None = None
    keyword_text: str | None = None
    semantic_text: str | None = None
    threshold_rank: float | None = None
    threshold_distance: float | None = None


@dataclass(frozen=True, slots=True)
class KeywordHit:
    entry: Entry
    score: float


@dataclass(frozen=True, slots=True)
class SemanticHit:
    entry: Entry
    distance: float


@dataclass(frozen=True, slots=True)
class _IndexedEntry:
    entry: Entry
    embedding: Sequence[float] | None


def _row_to_entry(r: sqlite3.Row) -> Entry:
    return Entry(
        id=r["id"],
        group_key=r["group_key"],
        group_ref=r["group_ref"],
        payload=json.loads(r["payload"]) if r["payload"] is not None else None,
        context=r["context"],
        keyword_text=r["keyword_text"],
        semantic_text=r["semantic_text"],
        threshold_rank=r["threshold_rank"],
        threshold_distance=r["threshold_distance"],
    )


def _serialize_vec(v: Sequence[float]) -> bytes:
    return struct.pack(f"<{len(v)}f", *v)


def add(
    conn: sqlite3.Connection,
    indexed: list[_IndexedEntry],
) -> list[Entry]:
    if not indexed:
        return []

    ids = [str(ULID()) for _ in indexed]

    batch = json.dumps(
        [
            {
                "id": id_,
                "group_key": i.entry.group_key,
                "group_ref": i.entry.group_ref,
                "payload": i.entry.payload,
                "context": i.entry.context,
                "keyword_text": i.entry.keyword_text,
                "semantic_text": i.entry.semantic_text,
                "threshold_rank": i.entry.threshold_rank,
                "threshold_distance": i.entry.threshold_distance,
            }
            for id_, i in zip(ids, indexed, strict=True)
        ]
    )

    cur = conn.execute(
        """
        INSERT INTO entry (
            id, group_key, group_ref, payload, context,
            keyword_text, semantic_text, threshold_rank, threshold_distance
        )
        SELECT
            value->>'id',
            value->>'group_key',
            value->>'group_ref',
            value->>'payload',
            value->>'context',
            value->>'keyword_text',
            value->>'semantic_text',
            value->>'threshold_rank',
            value->>'threshold_distance'
        FROM json_each(?)
        RETURNING
            id, group_key, group_ref, payload, context,
            keyword_text, semantic_text, threshold_rank, threshold_distance
        """,
        (batch,),
    )

    rows = list(cur)

    vec_rows = [
        (ids[idx], i.entry.group_key, _serialize_vec(i.embedding))
        for idx, i in enumerate(indexed)
        if i.embedding is not None
    ]
    if vec_rows:
        conn.executemany(
            "INSERT INTO entry_vec(id, group_key, embedding) VALUES (?, ?, ?)",
            vec_rows,
        )

    return [_row_to_entry(r) for r in rows]


def update(
    conn: sqlite3.Connection,
    entries: list[Entry],
) -> list[Entry]:
    if not entries:
        return []

    batch = json.dumps(
        [
            {
                "id": e.id,
                "group_ref": e.group_ref,
                "payload": e.payload,
                "context": e.context,
                "threshold_rank": e.threshold_rank,
                "threshold_distance": e.threshold_distance,
            }
            for e in entries
        ]
    )

    cur = conn.execute(
        """
        UPDATE entry
        SET group_ref          = src.group_ref,
            payload            = src.payload,
            context            = src.context,
            threshold_rank     = src.threshold_rank,
            threshold_distance = src.threshold_distance
        FROM (
            SELECT
                value->>'id'                 AS id,
                value->>'group_ref'          AS group_ref,
                value->>'payload'            AS payload,
                value->>'context'            AS context,
                value->>'threshold_rank'     AS threshold_rank,
                value->>'threshold_distance' AS threshold_distance
            FROM json_each(?)
        ) AS src
        WHERE entry.id = src.id
        RETURNING
            id, group_key, group_ref, payload, context,
            keyword_text, semantic_text, threshold_rank, threshold_distance
        """,
        (batch,),
    )

    return [_row_to_entry(r) for r in cur]


def remove(conn: sqlite3.Connection, ids: list[str]) -> list[str]:
    if not ids:
        return []

    cur = conn.execute(
        """
        DELETE FROM entry
        WHERE id IN (SELECT value FROM json_each(?))
        RETURNING id
        """,
        (json.dumps(ids),),
    )

    return [r["id"] for r in cur]


@dataclass(frozen=True, slots=True)
class Filters:
    id: list[str] | None = None
    group_key: list[str] | None = None
    group_ref: list[str] | None = None


_FETCH_SQL = """
SELECT id, group_key, group_ref, payload, context,
       keyword_text, semantic_text, threshold_rank, threshold_distance
FROM entry
WHERE (:ids IS NULL OR id IN (SELECT value FROM json_each(:ids)))
  AND (:group_keys IS NULL OR group_key IN (SELECT value FROM json_each(:group_keys)))
  AND (:group_refs IS NULL OR group_ref IN (SELECT value FROM json_each(:group_refs)))
"""


def fetch(
    conn: sqlite3.Connection,
    filters: Filters | None = None,
) -> list[Entry]:
    f = filters or Filters()
    cur = conn.execute(
        _FETCH_SQL,
        {
            "ids": json.dumps(f.id) if f.id else None,
            "group_keys": json.dumps(f.group_key) if f.group_key else None,
            "group_refs": json.dumps(f.group_ref) if f.group_ref else None,
        },
    )
    return [_row_to_entry(r) for r in cur]


_KEYWORD_SEARCH_SQL = """
SELECT e.id, e.group_key, e.group_ref, e.payload, e.context,
       e.keyword_text, e.semantic_text, e.threshold_rank, e.threshold_distance,
       -bm25(entry_fts) AS score
FROM entry_fts
JOIN entry e ON e.rowid = entry_fts.rowid
WHERE entry_fts MATCH :query
  AND (:ids IS NULL OR e.id IN (SELECT value FROM json_each(:ids)))
  AND (:group_keys IS NULL OR e.group_key IN (SELECT value FROM json_each(:group_keys)))
  AND (:group_refs IS NULL OR e.group_ref IN (SELECT value FROM json_each(:group_refs)))
ORDER BY bm25(entry_fts)
"""


def keyword_search(
    conn: sqlite3.Connection,
    query: str,
    filters: Filters | None = None,
    limit: int | None = None,
) -> list[KeywordHit]:
    if not query.strip():
        raise ValueError("search query must be non-empty")

    f = filters or Filters()
    sql = _KEYWORD_SEARCH_SQL
    params: dict[str, Any] = {
        "query": query,
        "ids": json.dumps(f.id) if f.id else None,
        "group_keys": json.dumps(f.group_key) if f.group_key else None,
        "group_refs": json.dumps(f.group_ref) if f.group_ref else None,
    }
    if limit is not None:
        sql = sql + "\nLIMIT :limit"
        params["limit"] = limit

    cur = conn.execute(sql, params)
    return [KeywordHit(entry=_row_to_entry(r), score=r["score"]) for r in cur]


_SEMANTIC_SEARCH_SQL = """
SELECT e.id, e.group_key, e.group_ref, e.payload, e.context,
       e.keyword_text, e.semantic_text, e.threshold_rank, e.threshold_distance,
       v.distance AS distance
FROM entry_vec v
JOIN entry e ON e.id = v.id
WHERE v.embedding MATCH :query
  AND v.group_key = :group_key
  AND k = :k
ORDER BY v.distance
"""

_SEMANTIC_SEARCH_NULL_SQL = """
SELECT e.id, e.group_key, e.group_ref, e.payload, e.context,
       e.keyword_text, e.semantic_text, e.threshold_rank, e.threshold_distance,
       v.distance AS distance
FROM entry_vec v
JOIN entry e ON e.id = v.id
WHERE v.embedding MATCH :query
  AND v.group_key IS NULL
  AND k = :k
ORDER BY v.distance
"""


def semantic_search(
    conn: sqlite3.Connection,
    embedding: Sequence[float],
    group_key: str | None,
    limit: int = 10,
) -> list[SemanticHit]:
    if not embedding:
        raise ValueError("embedding must be non-empty")

    if group_key is None:
        sql = _SEMANTIC_SEARCH_NULL_SQL
        params: dict[str, Any] = {
            "query": _serialize_vec(embedding),
            "k": limit,
        }
    else:
        sql = _SEMANTIC_SEARCH_SQL
        params = {
            "query": _serialize_vec(embedding),
            "group_key": group_key,
            "k": limit,
        }

    cur = conn.execute(sql, params)
    return [SemanticHit(entry=_row_to_entry(r), distance=r["distance"]) for r in cur]
