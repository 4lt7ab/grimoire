import json
import sqlite3
import struct
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from ulid import ULID

_ORDINAL_COLUMNS: frozenset[str] = frozenset(
    {"ordinal_1", "ordinal_2", "ordinal_3", "ordinal_4", "ordinal_5"}
)

_ENTRY_IDX_COLUMNS: frozenset[str] = (
    frozenset({"uniq_id", "uniq_ref", "group_ref"}) | _ORDINAL_COLUMNS
)


@dataclass(frozen=True, slots=True)
class Entry:
    """Identity row: a uniq_id and a JSON-serializable data blob."""

    uniq_id: str | None
    data: Any = None


@dataclass(frozen=True, slots=True)
class EntryIndex:
    """Filterable/searchable metadata sidecar keyed by uniq_id.

    All slots except `uniq_id` are optional. The five `ordinal_*` columns
    are BLOB-affinity — store any JSON-serializable scalar; SQLite holds
    each value in its native storage class. Library writes/reads them
    verbatim; semantics (what `ordinal_2` measures) are the caller's to
    define.
    """

    uniq_id: str | None
    uniq_ref: str | None = None
    group_ref: str | None = None
    ordinal_1: Any = None
    ordinal_2: Any = None
    ordinal_3: Any = None
    ordinal_4: Any = None
    ordinal_5: Any = None


@dataclass(frozen=True, slots=True)
class Filters:
    """Generic, dict-driven filters over `entry_idx` columns.

    `equals` keys may name any entry_idx column and apply
    `column IN (values...)`. `gte` / `lte` keys must name one of the five
    `ordinal_*` columns and apply `>= value` / `<= value`. Empty value
    lists in `equals` skip the filter (no-op). Filter values may be any
    type SQLite can compare — comparison follows SQLite's storage-class
    precedence (NULL < INT/REAL < TEXT < BLOB).
    """

    equals: dict[str, list[Any]] | None = None
    gte: dict[str, Any] | None = None
    lte: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class KeywordHit:
    uniq_id: str
    score: float


@dataclass(frozen=True, slots=True)
class SemanticHit:
    uniq_id: str
    distance: float


def _row_to_entry(r: sqlite3.Row) -> Entry:
    return Entry(
        uniq_id=r["uniq_id"],
        data=json.loads(r["data"]) if r["data"] is not None else None,
    )


def _row_to_entry_idx(r: sqlite3.Row) -> EntryIndex:
    return EntryIndex(
        uniq_id=r["uniq_id"],
        uniq_ref=r["uniq_ref"],
        group_ref=r["group_ref"],
        ordinal_1=r["ordinal_1"],
        ordinal_2=r["ordinal_2"],
        ordinal_3=r["ordinal_3"],
        ordinal_4=r["ordinal_4"],
        ordinal_5=r["ordinal_5"],
    )


def _serialize_vec(v: Sequence[float]) -> bytes:
    return struct.pack(f"<{len(v)}f", *v)


def _build_where(
    filters: Filters | None, alias: str = "i"
) -> tuple[list[str], dict[str, Any]]:
    """Compile a Filters into WHERE-clause fragments + named params.

    Caller joins the fragments with ' AND '. Returns ([], {}) for None or
    an empty Filters. Raises ValueError for unknown filter columns.
    """
    if filters is None:
        return [], {}

    clauses: list[str] = []
    params: dict[str, Any] = {}

    if filters.equals:
        for i, (col, values) in enumerate(filters.equals.items()):
            if col not in _ENTRY_IDX_COLUMNS:
                raise ValueError(
                    f"equals filter column {col!r} must be one of "
                    f"{sorted(_ENTRY_IDX_COLUMNS)}"
                )
            if not values:
                continue
            key = f"eq_{i}"
            clauses.append(f"{alias}.{col} IN (SELECT value FROM json_each(:{key}))")
            params[key] = json.dumps(list(values))

    if filters.gte:
        for i, (col, value) in enumerate(filters.gte.items()):
            if col not in _ORDINAL_COLUMNS:
                raise ValueError(
                    f"gte filter column {col!r} must be one of "
                    f"{sorted(_ORDINAL_COLUMNS)}"
                )
            key = f"gte_{i}"
            clauses.append(f"{alias}.{col} >= :{key}")
            params[key] = value

    if filters.lte:
        for i, (col, value) in enumerate(filters.lte.items()):
            if col not in _ORDINAL_COLUMNS:
                raise ValueError(
                    f"lte filter column {col!r} must be one of "
                    f"{sorted(_ORDINAL_COLUMNS)}"
                )
            key = f"lte_{i}"
            clauses.append(f"{alias}.{col} <= :{key}")
            params[key] = value

    return clauses, params


def _check_ids_exist(conn: sqlite3.Connection, ids: list[str]) -> None:
    """Raise ValueError if any id is missing from `entry` (the identity table)."""
    existing = conn.execute(
        "SELECT uniq_id FROM entry WHERE uniq_id IN (SELECT value FROM json_each(?))",
        (json.dumps(ids),),
    ).fetchall()
    known = {r["uniq_id"] for r in existing}
    for uniq_id in ids:
        if uniq_id not in known:
            raise ValueError(f"No entry with uniq_id {uniq_id!r}")


# ----------------------------------------------------------------------
# entry  (identity table)
# ----------------------------------------------------------------------


def add(conn: sqlite3.Connection, entries: list[Entry]) -> list[Entry]:
    """Insert entries with freshly-minted ULIDs. Returns entries with assigned ids."""
    if not entries:
        return []

    saved = [Entry(uniq_id=str(ULID()), data=e.data) for e in entries]
    conn.executemany(
        "INSERT INTO entry (uniq_id, data) VALUES (?, ?)",
        [
            (e.uniq_id, json.dumps(e.data) if e.data is not None else None)
            for e in saved
        ],
    )
    return saved


def update(conn: sqlite3.Connection, entries: list[Entry]) -> list[Entry]:
    """Rewrite the `data` column on existing rows, keyed by `uniq_id`.

    Unknown ids are silently skipped; the returned list contains only the
    entries that matched a row.
    """
    if not entries:
        return []

    saved: list[Entry] = []
    for e in entries:
        cur = conn.execute(
            "UPDATE entry SET data = ? WHERE uniq_id = ?",
            (json.dumps(e.data) if e.data is not None else None, e.uniq_id),
        )
        if cur.rowcount > 0:
            saved.append(e)
    return saved


def remove(conn: sqlite3.Connection, uniq_ids: list[str]) -> list[str]:
    """Delete entries by uniq_id. Sidecar rows are cascade-cleaned by trigger.

    Returns the ids that were actually removed (existed in `entry`).
    """
    if not uniq_ids:
        return []

    cur = conn.execute(
        "DELETE FROM entry "
        "WHERE uniq_id IN (SELECT value FROM json_each(?)) "
        "RETURNING uniq_id",
        (json.dumps(uniq_ids),),
    )
    return [r["uniq_id"] for r in cur]


def get(conn: sqlite3.Connection, uniq_ids: list[str]) -> list[Entry]:
    """Fetch entries by uniq_id. Returns only those that exist, no order guarantee."""
    if not uniq_ids:
        return []
    cur = conn.execute(
        "SELECT uniq_id, data FROM entry "
        "WHERE uniq_id IN (SELECT value FROM json_each(?))",
        (json.dumps(uniq_ids),),
    )
    return [_row_to_entry(r) for r in cur]


def fetch_by_uniq_ref(
    conn: sqlite3.Connection, uniq_refs: list[str]
) -> tuple[list[Entry], list[EntryIndex]]:
    """Fetch entries whose entry_idx row has uniq_ref in the given list.

    Returns parallel `(entries, indexes)` lists — `entries[i]` and
    `indexes[i]` describe the same row. `uniq_ref` is sparse-unique
    (UNIQUE index on the non-NULL rows), so each ref maps to at most
    one entry; entries without an entry_idx row are excluded.
    """
    if not uniq_refs:
        return [], []
    cur = conn.execute(
        "SELECT e.uniq_id, e.data, i.uniq_ref, i.group_ref, "
        "       i.ordinal_1, i.ordinal_2, i.ordinal_3, i.ordinal_4, i.ordinal_5 "
        "FROM entry e "
        "JOIN entry_idx i ON i.uniq_id = e.uniq_id "
        "WHERE i.uniq_ref IN (SELECT value FROM json_each(?))",
        (json.dumps(uniq_refs),),
    )
    entries: list[Entry] = []
    indexes: list[EntryIndex] = []
    for r in cur:
        entries.append(_row_to_entry(r))
        indexes.append(_row_to_entry_idx(r))
    return entries, indexes


# ----------------------------------------------------------------------
# entry_idx  (filterable metadata sidecar)
# ----------------------------------------------------------------------


def entry_idx_set(conn: sqlite3.Connection, indexes: list[EntryIndex]) -> list[str]:
    """Insert or replace entry_idx rows. Each uniq_id must exist in `entry`.

    Returns the list of ids that were written.
    """
    if not indexes:
        return []

    ids = [i.uniq_id for i in indexes if i.uniq_id is not None]
    if len(ids) != len(indexes):
        raise ValueError("EntryIndex.uniq_id is required for entry_idx_set")
    _check_ids_exist(conn, ids)

    conn.execute(
        "DELETE FROM entry_idx WHERE uniq_id IN (SELECT value FROM json_each(?))",
        (json.dumps(ids),),
    )
    conn.executemany(
        "INSERT INTO entry_idx "
        "(uniq_id, uniq_ref, group_ref, "
        " ordinal_1, ordinal_2, ordinal_3, ordinal_4, ordinal_5) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (
                i.uniq_id,
                i.uniq_ref,
                i.group_ref,
                i.ordinal_1,
                i.ordinal_2,
                i.ordinal_3,
                i.ordinal_4,
                i.ordinal_5,
            )
            for i in indexes
        ],
    )
    return ids


def fetch_idx(
    conn: sqlite3.Connection,
    filters: Filters | None = None,
    limit: int = 100,
    cursor: str | None = None,
    ascending: bool = True,
) -> tuple[list[Entry], list[EntryIndex]]:
    """Walk entry_idx rows ordered by `uniq_id`, ascending by default.

    Returns parallel `(entries, indexes)` lists. `cursor`, if given,
    returns rows on the far side of it in the walk direction — `uniq_id
    > cursor` ascending, `uniq_id < cursor` descending — so keyset
    paging works either way. For ordinal-window paging, pass
    `Filters(gte={...}, lte={...})`.
    """
    if limit < 1:
        raise ValueError("limit must be >= 1")
    clauses, params = _build_where(filters, alias="i")
    if cursor is not None:
        clauses.append("i.uniq_id > :cursor" if ascending else "i.uniq_id < :cursor")
        params["cursor"] = cursor

    sql = (
        "SELECT i.uniq_id, i.uniq_ref, i.group_ref, "
        "       i.ordinal_1, i.ordinal_2, i.ordinal_3, i.ordinal_4, i.ordinal_5, "
        "       e.data "
        "FROM entry_idx i "
        "JOIN entry e ON e.uniq_id = i.uniq_id"
    )
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += f" ORDER BY i.uniq_id {'ASC' if ascending else 'DESC'} LIMIT :limit"
    params["limit"] = limit

    entries: list[Entry] = []
    indexes: list[EntryIndex] = []
    for r in conn.execute(sql, params):
        entries.append(_row_to_entry(r))
        indexes.append(_row_to_entry_idx(r))
    return entries, indexes


# ----------------------------------------------------------------------
# entry_fts  (FTS5 keyword sidecar)
# ----------------------------------------------------------------------


def keyword(conn: sqlite3.Connection, items: list[tuple[str, str]]) -> list[str]:
    """Write (or replace) entry_fts rows for (uniq_id, text) pairs.

    Each id must exist in `entry`; text must be non-empty. Returns the
    ids that were written.
    """
    if not items:
        return []

    for _, text in items:
        if not text.strip():
            raise ValueError("text must be non-empty")

    ids = [i for i, _ in items]
    _check_ids_exist(conn, ids)

    conn.execute(
        "DELETE FROM entry_fts WHERE uniq_id IN (SELECT value FROM json_each(?))",
        (json.dumps(ids),),
    )
    conn.executemany(
        "INSERT INTO entry_fts(uniq_id, text) VALUES (?, ?)",
        items,
    )
    return ids


def keyword_search(
    conn: sqlite3.Connection,
    query: str,
    filters: Filters | None = None,
    limit: int = 10,
) -> tuple[list[Entry], list[KeywordHit]]:
    """FTS5 BM25 search. Filters apply via JOIN to entry_idx.

    Returns parallel `(entries, hits)` lists in BM25 rank order. `limit`
    defaults to 10 to avoid unbounded with-data joins.
    """
    if not query.strip():
        raise ValueError("search query must be non-empty")
    if limit < 1:
        raise ValueError("limit must be >= 1")

    clauses, params = _build_where(filters, alias="i")
    sql = (
        "SELECT entry_fts.uniq_id AS uniq_id, -bm25(entry_fts) AS score, "
        "       e.data "
        "FROM entry_fts "
        "JOIN entry e ON e.uniq_id = entry_fts.uniq_id "
    )
    if clauses:
        sql += "JOIN entry_idx i ON i.uniq_id = entry_fts.uniq_id "
    sql += "WHERE entry_fts MATCH :query"
    if clauses:
        sql += " AND " + " AND ".join(clauses)
    sql += " ORDER BY bm25(entry_fts)"
    params["query"] = query
    sql += " LIMIT :limit"
    params["limit"] = limit

    entries: list[Entry] = []
    hits: list[KeywordHit] = []
    for r in conn.execute(sql, params):
        entries.append(_row_to_entry(r))
        hits.append(KeywordHit(uniq_id=r["uniq_id"], score=r["score"]))
    return entries, hits


# ----------------------------------------------------------------------
# entry_vec  (vec0 semantic sidecar)
# ----------------------------------------------------------------------


def embed(
    conn: sqlite3.Connection,
    indexed: list[tuple[str, str, Sequence[float]]],
) -> list[str]:
    """Write (or replace) entry_vec rows for (uniq_id, text, embedding) triples.

    Each id must exist in `entry`; text must be non-empty. Returns the
    ids that were written.
    """
    if not indexed:
        return []

    for _, text, _ in indexed:
        if not text.strip():
            raise ValueError("text must be non-empty")

    ids = [i for i, _, _ in indexed]
    _check_ids_exist(conn, ids)

    conn.execute(
        "DELETE FROM entry_vec WHERE uniq_id IN (SELECT value FROM json_each(?))",
        (json.dumps(ids),),
    )
    conn.executemany(
        "INSERT INTO entry_vec(uniq_id, text, embedding) VALUES (?, ?, ?)",
        [(id_, text, _serialize_vec(vec)) for id_, text, vec in indexed],
    )
    return ids


def semantic_search(
    conn: sqlite3.Connection,
    embedding: Sequence[float],
    limit: int = 10,
) -> tuple[list[Entry], list[SemanticHit]]:
    """vec0 KNN search. Returns parallel `(entries, hits)` lists,
    nearest-first by vector distance."""
    if not embedding:
        raise ValueError("embedding must be non-empty")
    if limit < 1:
        raise ValueError("limit must be >= 1")

    entries: list[Entry] = []
    hits: list[SemanticHit] = []
    cur = conn.execute(
        "SELECT v.uniq_id AS uniq_id, v.distance AS distance, e.data "
        "FROM entry_vec v "
        "JOIN entry e ON e.uniq_id = v.uniq_id "
        "WHERE v.embedding MATCH :query AND k = :k "
        "ORDER BY v.distance",
        {
            "query": _serialize_vec(embedding),
            "k": limit,
        },
    )
    for r in cur:
        entries.append(_row_to_entry(r))
        hits.append(SemanticHit(uniq_id=r["uniq_id"], distance=r["distance"]))
    return entries, hits
