import sqlite3

from grimoire.embedder import Embedder
from grimoire.errors import GrimoireMismatch, InvalidEmbedder, SchemaVersionError

SCHEMA_VERSION = 1


def bootstrap(conn: sqlite3.Connection, embedder: Embedder) -> None:
    _validate_embedder(embedder)
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version == 0:
        conn.executescript(
            f"""
            CREATE TABLE grimoire (
                id        INTEGER PRIMARY KEY CHECK (id = 1),
                model     TEXT NOT NULL,
                dimension INTEGER NOT NULL
            );
            CREATE TABLE entries (
                id         TEXT PRIMARY KEY,
                kind       TEXT NOT NULL,
                content    TEXT NOT NULL,
                payload    TEXT,
                threshold  REAL
            );
            CREATE INDEX entries_kind ON entries(kind);
            CREATE VIRTUAL TABLE vectors USING vec0(
                entry_id  TEXT PRIMARY KEY,
                kind      TEXT partition key,
                embedding FLOAT[{embedder.dimension}]
            );
            """
        )
        conn.execute(
            "INSERT INTO grimoire (id, model, dimension) VALUES (1, ?, ?)",
            (embedder.model, embedder.dimension),
        )
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        conn.commit()
        return
    if version != SCHEMA_VERSION:
        raise SchemaVersionError(
            f"Database schema version is {version}, library expects {SCHEMA_VERSION}"
        )
    row = conn.execute("SELECT model, dimension FROM grimoire WHERE id = 1").fetchone()
    if row is None:
        raise SchemaVersionError("Database is missing its grimoire row")
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
