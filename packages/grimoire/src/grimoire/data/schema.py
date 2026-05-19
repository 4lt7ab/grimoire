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
    uniq_id TEXT PRIMARY KEY,
    data    TEXT
);

CREATE TABLE entry_idx (
    uniq_id   TEXT PRIMARY KEY,
    uniq_ref  TEXT,
    nominal_1 TEXT,
    nominal_2 TEXT,
    ordinal_1 REAL,
    ordinal_2 REAL,
    ordinal_3 REAL
);

CREATE INDEX entry_idx_uniq_ref  ON entry_idx(uniq_ref);
CREATE INDEX entry_idx_nominal_1 ON entry_idx(nominal_1);
CREATE INDEX entry_idx_nominal_2 ON entry_idx(nominal_2);
CREATE INDEX entry_idx_ordinal_1 ON entry_idx(ordinal_1);
CREATE INDEX entry_idx_ordinal_2 ON entry_idx(ordinal_2);
CREATE INDEX entry_idx_ordinal_3 ON entry_idx(ordinal_3);

CREATE VIRTUAL TABLE entry_fts USING fts5(
    uniq_id UNINDEXED,
    text
);

CREATE VIRTUAL TABLE entry_vec USING vec0(
    uniq_id TEXT PRIMARY KEY,
    +text TEXT,
    embedding float[{dimension}]
);

CREATE TRIGGER entry_delete_cascade
AFTER DELETE ON entry
FOR EACH ROW
BEGIN
    DELETE FROM entry_idx WHERE uniq_id = OLD.uniq_id;
    DELETE FROM entry_fts WHERE uniq_id = OLD.uniq_id;
    DELETE FROM entry_vec WHERE uniq_id = OLD.uniq_id;
END;
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
