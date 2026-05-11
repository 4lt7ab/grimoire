import sqlite3

from grimoire.data import meta
from grimoire.errors import SchemaVersionError

SCHEMA_VERSION = 1


def _ddl(dimension: int) -> str:
    return f"""
CREATE TABLE meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE entry (
    id                 TEXT PRIMARY KEY,
    group_key          TEXT,
    group_ref          TEXT,
    payload            TEXT,
    context            TEXT,
    keyword_text       TEXT,
    semantic_text      TEXT,
    threshold_rank     REAL,
    threshold_distance REAL
);

CREATE INDEX entry_group_key ON entry(group_key);

CREATE VIRTUAL TABLE entry_fts USING fts5(
    keyword_text,
    content='entry',
    content_rowid='rowid'
);

CREATE TRIGGER entry_ai AFTER INSERT ON entry BEGIN
    INSERT INTO entry_fts(rowid, keyword_text) VALUES (new.rowid, new.keyword_text);
END;

CREATE TRIGGER entry_ad AFTER DELETE ON entry BEGIN
    INSERT INTO entry_fts(entry_fts, rowid, keyword_text) VALUES ('delete', old.rowid, old.keyword_text);
END;

CREATE TRIGGER entry_au AFTER UPDATE ON entry BEGIN
    INSERT INTO entry_fts(entry_fts, rowid, keyword_text) VALUES ('delete', old.rowid, old.keyword_text);
    INSERT INTO entry_fts(rowid, keyword_text) VALUES (new.rowid, new.keyword_text);
END;

CREATE VIRTUAL TABLE entry_vec USING vec0(
    group_key TEXT PARTITION KEY,
    embedding float[{dimension}]
);
"""


def create(conn: sqlite3.Connection, *, model: str, dimension: int) -> None:
    conn.executescript(_ddl(dimension))
    conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    meta.add(conn, "model", model)
    meta.add(conn, "dimension", str(dimension))
    conn.commit()


def read_version(conn: sqlite3.Connection) -> int:
    return conn.execute("PRAGMA user_version").fetchone()[0]


def validate(conn: sqlite3.Connection) -> None:
    version = read_version(conn)
    if version != SCHEMA_VERSION:
        raise SchemaVersionError(
            f"Database schema version is {version}, library expects {SCHEMA_VERSION}. "
            f"Pre-v1 grimoire does not migrate in place — export, re-init, re-import."
        )
