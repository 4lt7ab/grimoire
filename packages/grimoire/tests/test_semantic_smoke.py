import sqlite3
import struct

import pytest

from grimoire.grimoire import open as open_grimoire


def _vec(values: list[float]) -> bytes:
    return struct.pack(f"<{len(values)}f", *values)


def test_semantic_search_returns_empty(tmp_path):
    db = tmp_path / "g.db"
    g = open_grimoire(db)
    assert g.semantic_search([0.0] * 384, group_key="anything") == []


def test_partition_null_insert(tmp_path):
    db = tmp_path / "g.db"
    g = open_grimoire(db)
    g.conn.execute(
        "INSERT INTO entry_vec (rowid, group_key, embedding) VALUES (?, ?, ?)",
        (1, None, _vec([0.1] * 384)),
    )
    rows = g.conn.execute(
        "SELECT rowid, group_key FROM entry_vec"
    ).fetchall()
    assert [tuple(r) for r in rows] == [(1, None)]


def test_partition_null_query(tmp_path):
    db = tmp_path / "g.db"
    g = open_grimoire(db)
    try:
        g.conn.execute(
            "INSERT INTO entry_vec (rowid, group_key, embedding) VALUES (?, ?, ?)",
            (1, None, _vec([1.0] + [0.0] * 383)),
        )
    except sqlite3.OperationalError as e:
        pytest.skip(f"NULL partition rejected at insert: {e}")

    query = _vec([1.0] + [0.0] * 383)

    eq_null = g.conn.execute(
        "SELECT rowid FROM entry_vec "
        "WHERE embedding MATCH ? AND group_key = NULL AND k = 5",
        (query,),
    ).fetchall()
    assert eq_null == [], "`= NULL` must always be false (SQLite 3VL)"

    is_null = g.conn.execute(
        "SELECT rowid FROM entry_vec "
        "WHERE embedding MATCH ? AND group_key IS NULL AND k = 5",
        (query,),
    ).fetchall()
    assert [tuple(r) for r in is_null] == [(1,)], (
        f"expected `IS NULL` to retrieve the NULL-partition row, got {is_null}"
    )


def test_semantic_search_null_partition(tmp_path):
    db = tmp_path / "g.db"
    g = open_grimoire(db)
    g.conn.execute(
        "INSERT INTO entry (rowid, id) VALUES (?, ?)",
        (1, "01NULLPART"),
    )
    g.conn.execute(
        "INSERT INTO entry_vec (rowid, group_key, embedding) VALUES (?, ?, ?)",
        (1, None, _vec([1.0] + [0.0] * 383)),
    )

    hits = g.semantic_search([1.0] + [0.0] * 383, group_key=None)
    assert len(hits) == 1
    assert hits[0].entry.id == "01NULLPART"
    assert hits[0].entry.group_key is None


def test_partition_isolation(tmp_path):
    db = tmp_path / "g.db"
    g = open_grimoire(db)
    g.conn.execute(
        "INSERT INTO entry_vec (rowid, group_key, embedding) VALUES (?, ?, ?)",
        (1, "alpha", _vec([1.0] + [0.0] * 383)),
    )
    g.conn.execute(
        "INSERT INTO entry_vec (rowid, group_key, embedding) VALUES (?, ?, ?)",
        (2, "beta", _vec([0.0, 1.0] + [0.0] * 382)),
    )
    query = _vec([0.5, 0.5] + [0.0] * 382)

    alpha = g.conn.execute(
        "SELECT rowid FROM entry_vec "
        "WHERE embedding MATCH ? AND group_key = ? AND k = 5",
        (query, "alpha"),
    ).fetchall()
    beta = g.conn.execute(
        "SELECT rowid FROM entry_vec "
        "WHERE embedding MATCH ? AND group_key = ? AND k = 5",
        (query, "beta"),
    ).fetchall()

    assert [r[0] for r in alpha] == [1]
    assert [r[0] for r in beta] == [2]
