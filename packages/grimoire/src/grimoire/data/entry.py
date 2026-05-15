import json
import sqlite3
import struct
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal

from ulid import ULID


@dataclass(frozen=True, slots=True)
class Entry:
    id: str | None
    group_key: str | None
    group_ref: str | None
    payload: dict[str, Any] | None
    context: str | None = None
    ordinal: float | None = None
    keyword_text: str | None = None
    threshold_rank: float | None = None
    semantic_text: str | None = None
    partition: str | None = None
    threshold_distance: float | None = None


@dataclass(frozen=True, slots=True)
class KeywordHit:
    entry: Entry
    score: float


@dataclass(frozen=True, slots=True)
class SemanticHit:
    entry: Entry
    distance: float


def _row_to_entry(r: sqlite3.Row) -> Entry:
    return Entry(
        id=r["id"],
        group_key=r["group_key"],
        group_ref=r["group_ref"],
        payload=json.loads(r["payload"]) if r["payload"] is not None else None,
        context=r["context"],
        ordinal=r["ordinal"],
        keyword_text=r["keyword_text"],
        threshold_rank=r["threshold_rank"],
        semantic_text=r["semantic_text"],
        partition=r["vec_partition"],
        threshold_distance=r["threshold_distance"],
    )


def _serialize_vec(v: Sequence[float]) -> bytes:
    return struct.pack(f"<{len(v)}f", *v)


def add(
    conn: sqlite3.Connection,
    entries: list[Entry],
) -> list[Entry]:
    if not entries:
        return []

    saved = [
        Entry(
            id=str(ULID()),
            group_key=e.group_key,
            group_ref=e.group_ref,
            payload=e.payload,
            context=e.context,
            ordinal=e.ordinal,
        )
        for e in entries
    ]
    try:
        conn.executemany(
            "INSERT INTO entry (id, group_key, group_ref, payload, context, ordinal) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            [
                (
                    e.id,
                    e.group_key,
                    e.group_ref,
                    json.dumps(e.payload) if e.payload is not None else None,
                    e.context,
                    e.ordinal,
                )
                for e in saved
            ],
        )
    except sqlite3.IntegrityError as exc:
        raise ValueError(
            "duplicate (group_key, group_ref); each (key, ref) pair must be unique"
        ) from exc
    return saved


def keyword_remove(conn: sqlite3.Connection, ids: list[str]) -> list[str]:
    """Delete entry_fts rows for the given ids. Returns the ids that had rows.

    Entries themselves are not affected.
    """
    if not ids:
        return []

    ids_json = json.dumps(ids)
    existing = conn.execute(
        "SELECT entry_id FROM entry_fts "
        "WHERE entry_id IN (SELECT value FROM json_each(?))",
        (ids_json,),
    ).fetchall()
    removed = [r["entry_id"] for r in existing]

    conn.execute(
        "DELETE FROM entry_fts WHERE entry_id IN (SELECT value FROM json_each(?))",
        (ids_json,),
    )
    return removed


def embed_remove(conn: sqlite3.Connection, ids: list[str]) -> list[str]:
    """Delete entry_vec rows for the given ids. Returns the ids that had rows.

    Entries themselves are not affected.
    """
    if not ids:
        return []

    ids_json = json.dumps(ids)
    existing = conn.execute(
        "SELECT id FROM entry_vec WHERE id IN (SELECT value FROM json_each(?))",
        (ids_json,),
    ).fetchall()
    removed = [r["id"] for r in existing]

    conn.execute(
        "DELETE FROM entry_vec WHERE id IN (SELECT value FROM json_each(?))",
        (ids_json,),
    )
    return removed


def keyword(
    conn: sqlite3.Connection,
    items: list[tuple[str, str]],
    *,
    threshold_rank: float | None = None,
) -> list[Entry]:
    """Write (or replace) entry_fts rows for the given (id, keyword_text) pairs.

    Existing fts rows for these ids are deleted first so the text or threshold
    can change freely. `threshold_rank` applies to every row in this batch.
    """
    if not items:
        return []

    for _, text in items:
        if not text.strip():
            raise ValueError("keyword_text must be non-empty")

    ids = [i for i, _ in items]
    conn.execute(
        "DELETE FROM entry_fts WHERE entry_id IN (SELECT value FROM json_each(?))",
        (json.dumps(ids),),
    )
    conn.executemany(
        "INSERT INTO entry_fts(entry_id, keyword_text, threshold_rank)"
        " VALUES (?, ?, ?)",
        [(id_, text, threshold_rank) for id_, text in items],
    )

    return fetch(conn, Filters(id=ids), limit=len(ids))


def embed(
    conn: sqlite3.Connection,
    indexed: list[tuple[str, str, Sequence[float]]],
    *,
    partition: str | None = None,
    threshold_distance: float | None = None,
) -> list[Entry]:
    """Write (or replace) entry_vec rows for the given triples.

    Existing vec rows for these ids are deleted first so the partition,
    threshold, or text can change freely.
    """
    if not indexed:
        return []

    ids = [i for i, _, _ in indexed]
    conn.execute(
        "DELETE FROM entry_vec WHERE id IN (SELECT value FROM json_each(?))",
        (json.dumps(ids),),
    )
    conn.executemany(
        "INSERT INTO entry_vec"
        "(id, partition, semantic_text, threshold_distance, embedding) "
        "VALUES (?, ?, ?, ?, ?)",
        [
            (id_, partition, text, threshold_distance, _serialize_vec(vec))
            for id_, text, vec in indexed
        ],
    )

    return fetch(conn, Filters(id=ids), limit=len(ids))


def update(
    conn: sqlite3.Connection,
    entries: list[Entry],
) -> list[Entry]:
    if not entries:
        return []

    saved: list[Entry] = []
    for e in entries:
        payload_text = json.dumps(e.payload) if e.payload is not None else None
        try:
            cur = conn.execute(
                "UPDATE entry "
                "SET group_key = ?, group_ref = ?, payload = ?, context = ?,"
                "    ordinal = ? "
                "WHERE id = ?",
                (e.group_key, e.group_ref, payload_text, e.context, e.ordinal, e.id),
            )
        except sqlite3.IntegrityError as exc:
            raise ValueError(
                "duplicate (group_key, group_ref); each (key, ref) pair must be unique"
            ) from exc
        if cur.rowcount > 0:
            saved.append(e)
    return saved


def remove(conn: sqlite3.Connection, ids: list[str]) -> list[str]:
    if not ids:
        return []

    ids_json = json.dumps(ids)
    cur = conn.execute(
        """
        DELETE FROM entry
        WHERE id IN (SELECT value FROM json_each(?))
        RETURNING id
        """,
        (ids_json,),
    )
    removed = [r["id"] for r in cur]

    # Cascade cleanup to virtual tables; FKs don't reach across to fts5/vec0.
    conn.execute(
        "DELETE FROM entry_fts WHERE entry_id IN (SELECT value FROM json_each(?))",
        (ids_json,),
    )
    conn.execute(
        "DELETE FROM entry_vec WHERE id IN (SELECT value FROM json_each(?))",
        (ids_json,),
    )

    return removed


@dataclass(frozen=True, slots=True)
class Filters:
    id: list[str] | None = None
    group_key: list[str] | None = None
    group_ref: list[str] | None = None
    ordinal_gte: float | None = None
    ordinal_lte: float | None = None


OrderBy = Literal["id", "ordinal"]

# NULLS LAST on the keyed column so unindexed entries don't bury the head of the
# result; id is the deterministic tiebreaker. Cursor pagination uses `e.id >
# :cursor` and is therefore only meaningful with the id-ASC default.
_ORDER_CLAUSES: dict[tuple[OrderBy, bool], str] = {
    ("id", False): "ORDER BY e.id ASC",
    ("id", True): "ORDER BY e.id DESC",
    ("ordinal", False): "ORDER BY e.ordinal ASC NULLS LAST, e.id ASC",
    ("ordinal", True): "ORDER BY e.ordinal DESC NULLS LAST, e.id ASC",
}

_FETCH_SQL = """
SELECT e.id, e.group_key, e.group_ref, e.payload, e.context, e.ordinal,
       fts.keyword_text AS keyword_text,
       fts.threshold_rank AS threshold_rank,
       v.semantic_text AS semantic_text,
       v.partition AS vec_partition,
       v.threshold_distance AS threshold_distance
FROM entry e
LEFT JOIN entry_fts fts ON fts.entry_id = e.id
LEFT JOIN entry_vec v ON v.id = e.id
WHERE (:ids IS NULL OR e.id IN (SELECT value FROM json_each(:ids)))
  AND (:group_keys IS NULL OR e.group_key IN (SELECT value FROM json_each(:group_keys)))
  AND (:group_refs IS NULL OR e.group_ref IN (SELECT value FROM json_each(:group_refs)))
  AND (:ordinal_gte IS NULL OR e.ordinal >= :ordinal_gte)
  AND (:ordinal_lte IS NULL OR e.ordinal <= :ordinal_lte)
  AND (:cursor IS NULL OR e.id > :cursor)
{order_by}
LIMIT :limit
"""


def fetch(
    conn: sqlite3.Connection,
    filters: Filters | None = None,
    limit: int = 100,
    cursor: str | None = None,
    order_by: OrderBy = "id",
    descending: bool = False,
) -> list[Entry]:
    """Fetch entries with their FTS5 and vec0 index rows attached.

    Default ordering is by id (chronologically, since ids are ULIDs). Pass
    `order_by="ordinal"` to sort by the consumer-supplied ordinal column;
    NULL ordinals sort last, with id as a deterministic tiebreaker.
    `descending=True` reverses the primary key direction.

    Entries with no keyword or semantic index row come back with the
    corresponding fields set to None. `cursor`, when given, returns entries
    with `id > cursor` — pass the id of the last entry from the previous
    page to walk forward. Cursor is id-based, so it only composes cleanly
    with the default `order_by="id"`, `descending=False`. For ordinal-window
    paging, use `Filters.ordinal_gte`/`ordinal_lte`.
    """
    clause = _ORDER_CLAUSES.get((order_by, descending))
    if clause is None:
        raise ValueError(
            f"order_by must be one of {sorted({k for k, _ in _ORDER_CLAUSES})};"
            f" got {order_by!r}"
        )

    f = filters or Filters()
    cur = conn.execute(
        _FETCH_SQL.format(order_by=clause),
        {
            "ids": json.dumps(f.id) if f.id else None,
            "group_keys": json.dumps(f.group_key) if f.group_key else None,
            "group_refs": json.dumps(f.group_ref) if f.group_ref else None,
            "ordinal_gte": f.ordinal_gte,
            "ordinal_lte": f.ordinal_lte,
            "cursor": cursor,
            "limit": limit,
        },
    )
    return [_row_to_entry(r) for r in cur]


def keyword_search(
    conn: sqlite3.Connection,
    query: str,
    filters: Filters | None = None,
    limit: int | None = None,
) -> list[KeywordHit]:
    if not query.strip():
        raise ValueError("search query must be non-empty")

    f = filters or Filters()
    sql = (
        "SELECT entry_fts.entry_id AS id, -bm25(entry_fts) AS score "
        "FROM entry_fts "
        "JOIN entry e ON e.id = entry_fts.entry_id "
        "WHERE entry_fts MATCH :query "
        "  AND (:ids IS NULL OR e.id IN (SELECT value FROM json_each(:ids))) "
        "  AND (:group_keys IS NULL OR e.group_key IN "
        "       (SELECT value FROM json_each(:group_keys))) "
        "  AND (:group_refs IS NULL OR e.group_ref IN "
        "       (SELECT value FROM json_each(:group_refs))) "
        "  AND (:ordinal_gte IS NULL OR e.ordinal >= :ordinal_gte) "
        "  AND (:ordinal_lte IS NULL OR e.ordinal <= :ordinal_lte) "
        "ORDER BY bm25(entry_fts)"
    )
    params: dict[str, Any] = {
        "query": query,
        "ids": json.dumps(f.id) if f.id else None,
        "group_keys": json.dumps(f.group_key) if f.group_key else None,
        "group_refs": json.dumps(f.group_ref) if f.group_ref else None,
        "ordinal_gte": f.ordinal_gte,
        "ordinal_lte": f.ordinal_lte,
    }
    if limit is not None:
        sql = sql + "\nLIMIT :limit"
        params["limit"] = limit

    matches = conn.execute(sql, params).fetchall()
    if not matches:
        return []

    ids_in_order = [r["id"] for r in matches]
    entries_by_id = {
        e.id: e for e in fetch(conn, Filters(id=ids_in_order), limit=len(ids_in_order))
    }
    return [KeywordHit(entry=entries_by_id[r["id"]], score=r["score"]) for r in matches]


_SEMANTIC_SEARCH_BY_PARTITION_SQL = """
SELECT v.id AS id, v.distance AS distance
FROM entry_vec v
WHERE v.embedding MATCH :query
  AND v.partition = :partition
  AND k = :k
ORDER BY v.distance
"""

_SEMANTIC_SEARCH_ANY_PARTITION_SQL = """
SELECT v.id AS id, v.distance AS distance
FROM entry_vec v
WHERE v.embedding MATCH :query
  AND k = :k
ORDER BY v.distance
"""


def semantic_search(
    conn: sqlite3.Connection,
    embedding: Sequence[float],
    partition: str | None,
    limit: int = 10,
) -> list[SemanticHit]:
    if not embedding:
        raise ValueError("embedding must be non-empty")

    if partition is None:
        sql = _SEMANTIC_SEARCH_ANY_PARTITION_SQL
        params: dict[str, Any] = {
            "query": _serialize_vec(embedding),
            "k": limit,
        }
    else:
        sql = _SEMANTIC_SEARCH_BY_PARTITION_SQL
        params = {
            "query": _serialize_vec(embedding),
            "partition": partition,
            "k": limit,
        }

    matches = conn.execute(sql, params).fetchall()
    if not matches:
        return []

    ids_in_order = [r["id"] for r in matches]
    entries_by_id = {
        e.id: e for e in fetch(conn, Filters(id=ids_in_order), limit=len(ids_in_order))
    }
    return [
        SemanticHit(entry=entries_by_id[r["id"]], distance=r["distance"])
        for r in matches
    ]
