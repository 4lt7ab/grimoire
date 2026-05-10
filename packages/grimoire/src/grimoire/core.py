from __future__ import annotations

import json
import shutil
import sqlite3
import struct
from collections.abc import Iterable, Mapping
from datetime import datetime
from pathlib import Path
from typing import Any, Self

import sqlite_vec
from ulid import ULID

from grimoire.embedder import Embedder
from grimoire.errors import GrimoireDestroyed, GrimoireNotFound
from grimoire.models import Entry
from grimoire.mount import (
    MODELS_DIRNAME,
    Mount,
    _db_path,
    _ensure_mount_dirs,
    _peek_file,
    _register,
    _resolve_mount,
    _unregister,
    _validate_name,
)
from grimoire.schema import create, validate

_WARMUP_PROBE = " "


class _Unset:
    """Sentinel for "field not supplied" in `update()`.

    `None` is a meaningful value for nullable fields (clears the column), so
    we can't reuse it to mean "leave alone". A dedicated sentinel keeps the
    two cases unambiguous.
    """

    _instance: _Unset | None = None

    def __new__(cls) -> _Unset:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __repr__(self) -> str:
        return "UNSET"


_UNSET: _Unset = _Unset()


class Grimoire:
    """A semantically-indexed datastore backed by one SQLite file.

    `Grimoire(name)` attaches to an existing database — auto-loading the
    embedder from the file's lock row via FastembedEmbedder, sharing the
    mount's `models/` cache. Raises `GrimoireNotFound` if the database is
    missing.

    `Grimoire(name, embedder=...)` attaches if the database exists
    (validating the embedder against the lock row, raising `GrimoireMismatch`
    on conflict) or creates a fresh database with that embedder if missing.
    The `embedder=` argument is the consent signal for creation: pass one to
    opt into materializing a new database, omit it to require an existing one.

    `name=None` targets the default DB at `<mount>/grimoire.db`. A name
    targets `<mount>/<name>/grimoire.db` and registers it in the manifest
    on creation (`description=` is stamped at creation only; it is silently
    ignored when attaching to an existing DB).
    """

    def __init__(
        self,
        name: str | None = None,
        *,
        mount: str | Path | Mount | None = None,
        embedder: Embedder | None = None,
        description: str | None = None,
        check_same_thread: bool = True,
    ) -> None:
        if name is not None:
            _validate_name(name)
        mount_path = _resolve_mount(mount)
        db = _db_path(mount_path, name)

        existed_before = db.exists()
        if not existed_before and embedder is None:
            label = "default database" if name is None else f"database {name!r}"
            raise GrimoireNotFound(f"No {label} at {db}")

        # Track the per-name subdir we materialize so a failed init leaves
        # nothing behind. The default DB writes directly into the mount
        # root, so there's no per-DB subdir to clean up.
        created_subdir: Path | None = None
        if not existed_before:
            _ensure_mount_dirs(mount_path)
            if name is not None:
                subdir = mount_path / name
                created_subdir = subdir if not subdir.exists() else None
                subdir.mkdir(parents=True, exist_ok=True)

        if embedder is None:
            embedder = _autoload_embedder(db, mount_path)

        conn = _open_conn(str(db), check_same_thread=check_same_thread)
        we_created_db = False
        try:
            if existed_before:
                # Attach branch: a file we didn't materialize must already be
                # a grimoire — never overwrite a stranger file's schema.
                validate(conn, embedder)
            else:
                # Create branch with race protection: another caller may have
                # raced us between our existence check and `_open_conn` and
                # already written the schema. If so, validate-and-attach
                # instead of clobbering.
                try:
                    existing = conn.execute(
                        "SELECT 1 FROM grimoire WHERE id = 1"
                    ).fetchone()
                except sqlite3.OperationalError:
                    existing = None
                if existing is None:
                    create(conn, embedder)
                    # Warm the embedder before handing control back so callers
                    # don't pay for a model fetch on their first add().
                    embedder.embed(_WARMUP_PROBE)
                    we_created_db = True
                else:
                    validate(conn, embedder)
        except BaseException:
            conn.close()
            # Only delete a file we materialized ourselves — never delete a
            # file that was already there when we arrived (race winner's data).
            if not existed_before:
                _unlink_db_files(db)
                if created_subdir is not None:
                    shutil.rmtree(created_subdir, ignore_errors=True)
            raise

        if we_created_db and name is not None:
            try:
                _register(
                    mount_path, name, model=embedder.model, description=description
                )
            except BaseException:
                conn.close()
                _unlink_db_files(db)
                if created_subdir is not None:
                    shutil.rmtree(created_subdir, ignore_errors=True)
                raise

        self._conn = conn
        self._embedder = embedder
        self._mount_path = mount_path
        self._name = name
        self._destroyed = False

    def _check_alive(self) -> None:
        if self._destroyed:
            label = (
                "default database" if self._name is None else f"database {self._name!r}"
            )
            raise GrimoireDestroyed(
                f"{label} at {self._mount_path} has been destroyed; handle is unusable"
            )

    def add(
        self,
        *,
        vector_text: str | None = None,
        keyword_text: str | None = None,
        group_key: str | None = None,
        group_ref: str | None = None,
        payload: dict[str, Any] | None = None,
        threshold: float | None = None,
    ) -> Entry:
        """Insert a single entry.

        Both `vector_text` and `keyword_text` are optional and independent.
        Pass `vector_text` to make the entry findable by vector_search;
        pass `keyword_text` to make it findable by keyword_search; pass
        both, or neither (a payload-only record retrievable by id /
        group_ref / list).
        """
        self._check_alive()
        entry_id = str(ULID())
        payload_json = json.dumps(payload) if payload is not None else None

        with self._conn:
            self._conn.execute(
                """
                INSERT INTO entries
                    (id, group_key, group_ref, vector_text, keyword_text,
                     payload, threshold)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    entry_id,
                    group_key,
                    group_ref,
                    vector_text,
                    keyword_text,
                    payload_json,
                    threshold,
                ),
            )
            if vector_text is not None:
                vector = self._embedder.embed(vector_text)
                self._conn.execute(
                    "INSERT INTO vectors (entry_id, group_key, embedding) "
                    "VALUES (?, ?, ?)",
                    (entry_id, group_key, _pack(vector)),
                )
            if keyword_text is not None:
                self._conn.execute(
                    "INSERT INTO entries_fts (keyword_text, entry_id) VALUES (?, ?)",
                    (keyword_text, entry_id),
                )

        return Entry(
            id=entry_id,
            group_key=group_key,
            group_ref=group_ref,
            vector_text=vector_text,
            keyword_text=keyword_text,
            payload=payload,
            threshold=threshold,
        )

    def add_many(self, records: Iterable[Mapping[str, Any]]) -> list[Entry]:
        """Insert many records in one transaction with one batched embed call.

        Each record is a mapping accepting the same keys as `add`'s kwargs:
        all are optional (`vector_text`, `keyword_text`, `group_key`,
        `group_ref`, `payload`, `threshold`). Returns the inserted entries
        in input order.

        The embedder is called once with only the records that supplied a
        `vector_text` — records without it skip vec0 entirely. Same for
        `keyword_text` and the FTS index.

        Atomic: if embedding or any insert fails, nothing is committed —
        unlike a loop over `add`, which would leave partial state behind.
        """
        self._check_alive()
        records = list(records)
        if not records:
            return []

        # Batch the embedder call across only those records that opted in.
        embed_indices = [
            i for i, r in enumerate(records) if r.get("vector_text") is not None
        ]
        embed_texts = [records[i]["vector_text"] for i in embed_indices]
        embedded = self._embedder.embed_many(embed_texts) if embed_texts else []
        blob_by_index = {
            idx: _pack(vec) for idx, vec in zip(embed_indices, embedded, strict=True)
        }

        entries: list[Entry] = []
        entries_rows: list[tuple] = []
        vectors_rows: list[tuple] = []
        fts_rows: list[tuple] = []

        for i, record in enumerate(records):
            entry_id = str(ULID())
            group_key = record.get("group_key")
            group_ref = record.get("group_ref")
            vector_text = record.get("vector_text")
            keyword_text = record.get("keyword_text")
            payload = record.get("payload")
            threshold = record.get("threshold")

            payload_json = json.dumps(payload) if payload is not None else None

            entries_rows.append(
                (
                    entry_id,
                    group_key,
                    group_ref,
                    vector_text,
                    keyword_text,
                    payload_json,
                    threshold,
                )
            )
            if vector_text is not None:
                vectors_rows.append((entry_id, group_key, blob_by_index[i]))
            if keyword_text is not None:
                fts_rows.append((keyword_text, entry_id))

            entries.append(
                Entry(
                    id=entry_id,
                    group_key=group_key,
                    group_ref=group_ref,
                    vector_text=vector_text,
                    keyword_text=keyword_text,
                    payload=payload,
                    threshold=threshold,
                )
            )

        with self._conn:
            self._conn.executemany(
                "INSERT INTO entries "
                "(id, group_key, group_ref, vector_text, keyword_text, "
                "payload, threshold) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                entries_rows,
            )
            if vectors_rows:
                self._conn.executemany(
                    "INSERT INTO vectors (entry_id, group_key, embedding) "
                    "VALUES (?, ?, ?)",
                    vectors_rows,
                )
            if fts_rows:
                self._conn.executemany(
                    "INSERT INTO entries_fts (keyword_text, entry_id) VALUES (?, ?)",
                    fts_rows,
                )

        return entries

    def get(self, entry_id: str) -> Entry | None:
        self._check_alive()
        row = self._conn.execute(
            "SELECT id, group_key, group_ref, vector_text, keyword_text, "
            "payload, threshold "
            "FROM entries WHERE id = ?",
            (entry_id,),
        ).fetchone()
        return _row_to_entry(row) if row is not None else None

    def get_by_group_ref(
        self, *, group_key: str | None, group_ref: str
    ) -> Entry | None:
        """Look up an entry by its consumer-set `group_ref` within a group.

        `group_ref` is a non-NULL, consumer-supplied identifier; uniqueness
        is enforced per `(group_key, group_ref)` pair, so the same `group_ref`
        can be reused across groups (and within the ungrouped namespace,
        where `group_key` is NULL). Returns None if no match.
        """
        self._check_alive()
        if group_key is None:
            row = self._conn.execute(
                "SELECT id, group_key, group_ref, vector_text, keyword_text, "
                "payload, threshold "
                "FROM entries WHERE group_key IS NULL AND group_ref = ?",
                (group_ref,),
            ).fetchone()
        else:
            row = self._conn.execute(
                "SELECT id, group_key, group_ref, vector_text, keyword_text, "
                "payload, threshold "
                "FROM entries WHERE group_key = ? AND group_ref = ?",
                (group_key, group_ref),
            ).fetchone()
        return _row_to_entry(row) if row is not None else None

    def list(
        self,
        *,
        group_key: str | None = None,
        group_ref: str | None = None,
        limit: int = 100,
        after_id: str | None = None,
        created_after: datetime | None = None,
        created_before: datetime | None = None,
    ) -> list[Entry]:
        self._check_alive()
        sql = (
            "SELECT id, group_key, group_ref, vector_text, keyword_text, "
            "payload, threshold "
            "FROM entries"
        )
        params: list[Any] = []
        clauses: list[str] = []
        if group_key is not None:
            clauses.append("group_key = ?")
            params.append(group_key)
        if group_ref is not None:
            clauses.append("group_ref = ?")
            params.append(group_ref)
        if after_id is not None:
            clauses.append("id > ?")
            params.append(after_id)
        if created_after is not None:
            clauses.append("id >= ?")
            params.append(_ulid_floor(created_after))
        if created_before is not None:
            clauses.append("id < ?")
            params.append(_ulid_floor(created_before))
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY id LIMIT ?"
        params.append(limit)

        rows = self._conn.execute(sql, params).fetchall()
        return [_row_to_entry(r) for r in rows]

    def update(
        self,
        entry_id: str,
        *,
        payload: dict[str, Any] | None | _Unset = _UNSET,
        threshold: float | None | _Unset = _UNSET,
    ) -> Entry | None:
        """Patch the mutable metadata fields on an entry.

        Only `payload` and `threshold` can be updated. The indexed and
        identity fields (`vector_text`, `keyword_text`, `group_key`,
        `group_ref`) are immutable after creation — to change them, delete
        the entry and add a fresh one. This keeps the embedder, the FTS
        index, and the vec0 partitions immutable too: no re-embed dance,
        no row shuffling, just a single SQL UPDATE.

        Omit a field to leave it alone; pass `None` to clear it; pass a
        value to replace it. Returns the updated entry, or `None` if the
        id is unknown.
        """
        self._check_alive()
        if isinstance(payload, _Unset) and isinstance(threshold, _Unset):
            return self.get(entry_id)

        current = self.get(entry_id)
        if current is None:
            return None

        new_payload = current.payload if isinstance(payload, _Unset) else payload
        new_threshold = (
            current.threshold if isinstance(threshold, _Unset) else threshold
        )
        payload_json = json.dumps(new_payload) if new_payload is not None else None

        with self._conn:
            self._conn.execute(
                "UPDATE entries SET payload = ?, threshold = ? WHERE id = ?",
                (payload_json, new_threshold, entry_id),
            )

        return Entry(
            id=entry_id,
            group_key=current.group_key,
            group_ref=current.group_ref,
            vector_text=current.vector_text,
            keyword_text=current.keyword_text,
            payload=new_payload,
            threshold=new_threshold,
        )

    def delete(self, entry_id: str) -> bool:
        self._check_alive()
        with self._conn:
            cursor = self._conn.execute("DELETE FROM entries WHERE id = ?", (entry_id,))
            if cursor.rowcount == 0:
                return False
            self._conn.execute("DELETE FROM vectors WHERE entry_id = ?", (entry_id,))
            self._conn.execute(
                "DELETE FROM entries_fts WHERE entry_id = ?", (entry_id,)
            )
        return True

    def delete_many(self, ids: Iterable[str]) -> list[bool]:
        """Delete many entries in one transaction.

        Returns one `bool` per input id in input order — `True` if the entry
        existed and was deleted, `False` otherwise. Duplicate ids each
        receive the same answer (their pre-call existence). Atomic: all
        successful deletes apply or none do.
        """
        self._check_alive()
        ids = list(ids)
        if not ids:
            return []

        unique = list(dict.fromkeys(ids))  # preserves first-seen order, deduped
        placeholders = ",".join(["?"] * len(unique))
        existing = {
            row[0]
            for row in self._conn.execute(
                f"SELECT id FROM entries WHERE id IN ({placeholders})", unique
            ).fetchall()
        }

        if existing:
            existing_list = list(existing)
            ph = ",".join(["?"] * len(existing_list))
            with self._conn:
                self._conn.execute(
                    f"DELETE FROM entries WHERE id IN ({ph})", existing_list
                )
                self._conn.execute(
                    f"DELETE FROM vectors WHERE entry_id IN ({ph})", existing_list
                )
                self._conn.execute(
                    f"DELETE FROM entries_fts WHERE entry_id IN ({ph})",
                    existing_list,
                )

        return [eid in existing for eid in ids]

    def vector_search(
        self,
        query: str,
        *,
        group_key: str | None = None,
        k: int = 10,
        dynamic_threshold: bool = False,
        created_after: datetime | None = None,
        created_before: datetime | None = None,
    ) -> list[Entry]:
        """Return up to `k` entries ranked by vector distance to `query`.

        Filters interact with the KNN in two different ways:

        - `group_key` is pushed into the vector index's partition key, so the
          KNN considers only entries of that group_key from the start.
        - `created_after`, `created_before`, and `dynamic_threshold` apply
          AFTER the KNN returns its top-k. With a narrow time window or
          tight per-record thresholds, this can return fewer than `k`
          results — even when many qualifying entries exist further down
          the similarity ranking. Raise `k` to compensate.
        """
        self._check_alive()
        vector = self._embedder.embed(query)

        sql = (
            "SELECT e.id, e.group_key, e.group_ref, e.vector_text, "
            "e.keyword_text, e.payload, e.threshold, v.distance "
            "FROM vectors v JOIN entries e ON e.id = v.entry_id "
            "WHERE v.embedding MATCH ? AND k = ?"
        )
        params: list[Any] = [_pack(vector), k]
        if group_key is not None:
            sql += " AND v.group_key = ?"
            params.append(group_key)
        if created_after is not None:
            sql += " AND e.id >= ?"
            params.append(_ulid_floor(created_after))
        if created_before is not None:
            sql += " AND e.id < ?"
            params.append(_ulid_floor(created_before))
        sql += " ORDER BY v.distance"

        rows = self._conn.execute(sql, params).fetchall()
        results = [
            Entry(
                id=r[0],
                group_key=r[1],
                group_ref=r[2],
                vector_text=r[3],
                keyword_text=r[4],
                payload=json.loads(r[5]) if r[5] is not None else None,
                threshold=r[6],
                distance=r[7],
            )
            for r in rows
        ]
        if dynamic_threshold:
            results = [
                r for r in results if r.threshold is None or r.distance <= r.threshold
            ]
        return results

    def keyword_search(
        self,
        query: str,
        *,
        group_key: str | None = None,
        k: int = 10,
        created_after: datetime | None = None,
        created_before: datetime | None = None,
    ) -> list[Entry]:
        self._check_alive()
        sql = (
            "SELECT e.id, e.group_key, e.group_ref, e.vector_text, "
            "e.keyword_text, e.payload, e.threshold, "
            "bm25(entries_fts) AS rank "
            "FROM entries_fts JOIN entries e ON e.id = entries_fts.entry_id "
            "WHERE entries_fts MATCH ?"
        )
        params: list[Any] = [query]
        if group_key is not None:
            sql += " AND e.group_key = ?"
            params.append(group_key)
        if created_after is not None:
            sql += " AND e.id >= ?"
            params.append(_ulid_floor(created_after))
        if created_before is not None:
            sql += " AND e.id < ?"
            params.append(_ulid_floor(created_before))
        sql += " ORDER BY bm25(entries_fts) LIMIT ?"
        params.append(k)

        rows = self._conn.execute(sql, params).fetchall()
        return [
            Entry(
                id=r[0],
                group_key=r[1],
                group_ref=r[2],
                vector_text=r[3],
                keyword_text=r[4],
                payload=json.loads(r[5]) if r[5] is not None else None,
                threshold=r[6],
                rank=r[7],
            )
            for r in rows
        ]

    def close(self) -> None:
        """Release the underlying connection. The handle is no longer usable for I/O."""
        self._conn.close()

    def destroy(self) -> None:
        """Close the connection, delete the file from disk, and invalidate this handle.

        Removes the SQLite file plus its WAL/SHM siblings, drops the
        manifest entry for named DBs, and best-effort removes the per-name
        subdirectory if it's empty. Idempotent on a half-deleted state. After
        this call, every other method on the handle raises `GrimoireDestroyed`.
        """
        self._check_alive()
        self._conn.close()
        db = _db_path(self._mount_path, self._name)
        _unlink_db_files(db)
        if self._name is not None:
            subdir = self._mount_path / self._name
            if subdir.exists() and not any(subdir.iterdir()):
                subdir.rmdir()
            _unregister(self._mount_path, self._name)
        self._destroyed = True

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def _autoload_embedder(db: Path, mount: Path) -> Embedder:
    """Reconstruct the embedder a file was created with from its lock row.

    Limited to fastembed-bundled models: given the model name, FastembedEmbedder
    can rebuild the embedder. Custom embedders that don't round-trip through a
    string name should be passed explicitly via `Grimoire(..., embedder=...)`.
    """
    stats = _peek_file(db)
    if stats is None:
        raise GrimoireNotFound(f"Not a grimoire file: {db}")
    try:
        from grimoire.embedders import FastembedEmbedder
    except ImportError as exc:
        raise ImportError(
            "Auto-loading an embedder requires the `fastembed` extra. "
            "Install with: pip install grimoire[fastembed], "
            "or pass embedder= explicitly to Grimoire."
        ) from exc
    return FastembedEmbedder(stats.model, cache_folder=mount / MODELS_DIRNAME)


def _open_conn(path: str, *, check_same_thread: bool = True) -> sqlite3.Connection:
    conn = sqlite3.connect(path, check_same_thread=check_same_thread)
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)
    conn.execute("PRAGMA foreign_keys = ON")
    # WAL lets readers coexist with one writer instead of blocking each other;
    # busy_timeout makes occasional multi-writer attempts queue at the SQLite
    # level rather than crash with `database is locked`. Default-on because
    # almost any caller wants this — the rollback-journal default is from a
    # different era. Sustained high-concurrency writes still serialize.
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


def _unlink_db_files(db: Path) -> None:
    """Remove the SQLite file plus its WAL/SHM/journal siblings."""
    for sibling in (
        db,
        db.parent / (db.name + "-wal"),
        db.parent / (db.name + "-shm"),
        db.parent / (db.name + "-journal"),
    ):
        sibling.unlink(missing_ok=True)


def _pack(vector: list[float]) -> bytes:
    return struct.pack(f"{len(vector)}f", *vector)


def _ulid_floor(dt: datetime) -> str:
    return str(ULID.from_datetime(dt))[:10] + "0" * 16


def _row_to_entry(row: tuple) -> Entry:
    return Entry(
        id=row[0],
        group_key=row[1],
        group_ref=row[2],
        vector_text=row[3],
        keyword_text=row[4],
        payload=json.loads(row[5]) if row[5] is not None else None,
        threshold=row[6],
    )
