import sqlite3

from grimoire.embedder import Embedder
from grimoire.errors import (
    GrimoireMismatch,
    GrimoireNotFound,
    InvalidEmbedder,
    SchemaVersionError,
)

SCHEMA_VERSION = 2


def create(conn: sqlite3.Connection, embedder: Embedder) -> None:
    """Write the grimoire schema and lock row into a fresh SQLite file."""
    _validate_embedder(embedder)
    conn.executescript(
        f"""
        CREATE TABLE grimoire (
            id        INTEGER PRIMARY KEY CHECK (id = 1),
            model     TEXT NOT NULL,
            dimension INTEGER NOT NULL
        );
        CREATE TABLE entries (
            id         TEXT PRIMARY KEY,
            group_key  TEXT,
            group_ref  TEXT,
            content    TEXT NOT NULL,
            keywords   TEXT,
            payload    TEXT,
            threshold  REAL
        );
        CREATE INDEX entries_group_key ON entries(group_key);
        -- Uniqueness on (group_key, group_ref) where group_ref is set.
        -- COALESCE collapses NULL group_key to '' so the ungrouped namespace
        -- is treated as a single group for dedupe, not as "no constraint"
        -- (SQLite's default UNIQUE treats NULLs as distinct, which would
        -- silently let consumers stack duplicate group_refs in that bucket).
        -- Caveat: a literal empty-string group_key shares this bucket with
        -- NULL group_key for uniqueness purposes — don't use "" as a group.
        CREATE UNIQUE INDEX entries_group_ref_uniq
            ON entries(COALESCE(group_key, ''), group_ref)
            WHERE group_ref IS NOT NULL;
        CREATE VIRTUAL TABLE vectors USING vec0(
            entry_id  TEXT PRIMARY KEY,
            group_key TEXT partition key,
            embedding FLOAT[{embedder.dimension}]
        );
        CREATE VIRTUAL TABLE entries_fts USING fts5(
            content,
            keywords,
            entry_id UNINDEXED
        );
        """
    )
    conn.execute(
        "INSERT INTO grimoire (id, model, dimension) VALUES (1, ?, ?)",
        (embedder.model, embedder.dimension),
    )
    conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    conn.commit()


def validate(conn: sqlite3.Connection, embedder: Embedder) -> None:
    """Confirm an open SQLite file is a grimoire whose lock matches the embedder."""
    _validate_embedder(embedder)
    try:
        row = conn.execute(
            "SELECT model, dimension FROM grimoire WHERE id = 1"
        ).fetchone()
    except sqlite3.OperationalError as exc:
        raise GrimoireNotFound(
            "Database has no grimoire table; not a grimoire file"
        ) from exc
    if row is None:
        raise GrimoireNotFound("Database is missing its grimoire row")
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version != SCHEMA_VERSION:
        raise SchemaVersionError(
            f"Database schema version is {version}, library expects {SCHEMA_VERSION}"
        )
    stored_model, stored_dim = row
    if stored_model != embedder.model or stored_dim != embedder.dimension:
        raise GrimoireMismatch(
            f"Embedder (model={embedder.model!r}, dim={embedder.dimension}) "
            f"does not match grimoire "
            f"(model={stored_model!r}, dim={stored_dim})"
        )


def _validate_embedder(embedder: Embedder) -> None:
    if not isinstance(embedder.dimension, int) or isinstance(embedder.dimension, bool):
        raise InvalidEmbedder(
            f"Embedder dimension must be an int, "
            f"got {type(embedder.dimension).__name__}"
        )
    if embedder.dimension <= 0:
        raise InvalidEmbedder(
            f"Embedder dimension must be positive, got {embedder.dimension}"
        )
    if not isinstance(embedder.model, str) or not embedder.model:
        raise InvalidEmbedder(
            f"Embedder model must be a non-empty string, got {embedder.model!r}"
        )
