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
    id        TEXT PRIMARY KEY,
    group_key TEXT,
    group_ref TEXT,
    payload   TEXT,
    context   TEXT
);

CREATE INDEX entry_group_key ON entry(group_key);

CREATE UNIQUE INDEX entry_group_ref_unique ON entry(group_key, group_ref)
    WHERE group_key IS NOT NULL AND group_ref IS NOT NULL;

CREATE VIRTUAL TABLE entry_fts USING fts5(
    entry_id UNINDEXED,
    keyword_text,
    threshold_rank UNINDEXED
);

CREATE VIRTUAL TABLE entry_vec USING vec0(
    id TEXT PRIMARY KEY,
    partition TEXT PARTITION KEY,
    +semantic_text TEXT,
    +threshold_distance FLOAT,
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
