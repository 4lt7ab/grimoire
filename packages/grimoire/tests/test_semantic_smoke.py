import sqlite3
import struct

import pytest

from grimoire.data import entry as entry_sql
from grimoire.grimoire import open as open_grimoire


def _vec(values: list[float]) -> bytes:
    return struct.pack(f"<{len(values)}f", *values)


def test_semantic_search_returns_empty(tmp_path, fake_embedder):
    db = tmp_path / "g.db"
    g = open_grimoire(db, embedder=fake_embedder)
    assert g.semantic_search("anything", partition="anything") == []


def test_partition_null_insert(tmp_path, fake_embedder):
    db = tmp_path / "g.db"
    g = open_grimoire(db, embedder=fake_embedder)
    g._conn.execute(
        "INSERT INTO entry_vec (id, partition, embedding) VALUES (?, ?, ?)",
        ("01NULLINS", None, _vec([0.1] * 384)),
    )
    rows = g._conn.execute(
        "SELECT id, partition FROM entry_vec"
    ).fetchall()
    assert [tuple(r) for r in rows] == [("01NULLINS", None)]


def test_partition_null_query(tmp_path, fake_embedder):
    db = tmp_path / "g.db"
    g = open_grimoire(db, embedder=fake_embedder)
    try:
        g._conn.execute(
            "INSERT INTO entry_vec (id, partition, embedding) VALUES (?, ?, ?)",
            ("01NULLQRY", None, _vec([1.0] + [0.0] * 383)),
        )
    except sqlite3.OperationalError as e:
        pytest.skip(f"NULL partition rejected at insert: {e}")

    query = _vec([1.0] + [0.0] * 383)

    eq_null = g._conn.execute(
        "SELECT id FROM entry_vec "
        "WHERE embedding MATCH ? AND partition = NULL AND k = 5",
        (query,),
    ).fetchall()
    assert eq_null == [], "`= NULL` must always be false (SQLite 3VL)"

    is_null = g._conn.execute(
        "SELECT id FROM entry_vec "
        "WHERE embedding MATCH ? AND partition IS NULL AND k = 5",
        (query,),
    ).fetchall()
    assert [tuple(r) for r in is_null] == [("01NULLQRY",)], (
        f"expected `IS NULL` to retrieve the NULL-partition row, got {is_null}"
    )


def test_semantic_search_null_partition(tmp_path, fake_embedder):
    db = tmp_path / "g.db"
    g = open_grimoire(db, embedder=fake_embedder)
    g._conn.execute(
        "INSERT INTO entry (id) VALUES (?)",
        ("01NULLPART",),
    )
    g._conn.execute(
        "INSERT INTO entry_vec (id, partition, embedding) VALUES (?, ?, ?)",
        ("01NULLPART", None, _vec([1.0] + [0.0] * 383)),
    )

    hits = entry_sql.semantic_search(g._conn, [1.0] + [0.0] * 383, partition=None)
    assert len(hits) == 1
    assert hits[0].entry.id == "01NULLPART"
    assert hits[0].entry.group_key is None


def test_partition_isolation(tmp_path, fake_embedder):
    db = tmp_path / "g.db"
    g = open_grimoire(db, embedder=fake_embedder)
    g._conn.execute(
        "INSERT INTO entry_vec (id, partition, embedding) VALUES (?, ?, ?)",
        ("01ALPHA", "alpha", _vec([1.0] + [0.0] * 383)),
    )
    g._conn.execute(
        "INSERT INTO entry_vec (id, partition, embedding) VALUES (?, ?, ?)",
        ("01BETA", "beta", _vec([0.0, 1.0] + [0.0] * 382)),
    )
    query = _vec([0.5, 0.5] + [0.0] * 382)

    alpha = g._conn.execute(
        "SELECT id FROM entry_vec "
        "WHERE embedding MATCH ? AND partition = ? AND k = 5",
        (query, "alpha"),
    ).fetchall()
    beta = g._conn.execute(
        "SELECT id FROM entry_vec "
        "WHERE embedding MATCH ? AND partition = ? AND k = 5",
        (query, "beta"),
    ).fetchall()

    assert [r[0] for r in alpha] == ["01ALPHA"]
    assert [r[0] for r in beta] == ["01BETA"]
