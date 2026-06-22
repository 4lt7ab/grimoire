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
) WITHOUT ROWID;

CREATE TABLE entry_idx (
    uniq_id   TEXT PRIMARY KEY,
    uniq_ref  TEXT,
    group_ref TEXT,
    owner_ref TEXT,
    ordinal_1,
    ordinal_2,
    ordinal_3,
    ordinal_4,
    ordinal_5
) WITHOUT ROWID;

CREATE UNIQUE INDEX entry_idx_uniq_ref
    ON entry_idx(uniq_ref) WHERE uniq_ref IS NOT NULL;

CREATE INDEX entry_idx_group_ref
    ON entry_idx(group_ref) WHERE group_ref IS NOT NULL;

CREATE INDEX entry_idx_owner_ref
    ON entry_idx(owner_ref) WHERE owner_ref IS NOT NULL;

CREATE INDEX entry_idx_o1_o2_o3_o4_o5
    ON entry_idx(ordinal_1, ordinal_2, ordinal_3, ordinal_4, ordinal_5)
    WHERE ordinal_1 IS NOT NULL;
CREATE INDEX entry_idx_o2_o3_o4_o5_o1
    ON entry_idx(ordinal_2, ordinal_3, ordinal_4, ordinal_5, ordinal_1)
    WHERE ordinal_2 IS NOT NULL;
CREATE INDEX entry_idx_o3_o4_o5_o1_o2
    ON entry_idx(ordinal_3, ordinal_4, ordinal_5, ordinal_1, ordinal_2)
    WHERE ordinal_3 IS NOT NULL;
CREATE INDEX entry_idx_o4_o5_o1_o2_o3
    ON entry_idx(ordinal_4, ordinal_5, ordinal_1, ordinal_2, ordinal_3)
    WHERE ordinal_4 IS NOT NULL;
CREATE INDEX entry_idx_o5_o1_o2_o3_o4
    ON entry_idx(ordinal_5, ordinal_1, ordinal_2, ordinal_3, ordinal_4)
    WHERE ordinal_5 IS NOT NULL;

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
    return int(conn.execute("PRAGMA user_version").fetchone()[0])


def validate(conn: sqlite3.Connection) -> None:
    version = read_version(conn)
    if version != SCHEMA_VERSION:
        raise SchemaVersionError(
            f"Database schema version is {version}, library expects {SCHEMA_VERSION}. "
            f"Pre-v1 grimoire does not migrate in place — export, re-init, re-import."
        )
