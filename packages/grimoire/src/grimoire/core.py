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
from grimoire.errors import DatabaseExists, GrimoireNotFound
from grimoire.models import Entry, Stats
from grimoire.mount import (
    MODELS_DIRNAME,
    Mount,
    _db_path,
    _ensure_mount_dirs,
    _register,
    _resolve_mount,
    _unregister,
    _validate_name,
)
from grimoire.schema import create, validate

_WARMUP_PROBE = " "

# BM25 column weights for keyword_search: (content, keywords).
# Keyword matches outrank content matches by this ratio. Tunable here.
KEYWORD_BM25_WEIGHTS = (1.0, 5.0)
_BM25_RANK = f"bm25(entries_fts, {KEYWORD_BM25_WEIGHTS[0]}, {KEYWORD_BM25_WEIGHTS[1]})"


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
    """A semantically-indexed datastore backed by one SQLite file."""

    def __init__(self, *, conn: sqlite3.Connection, embedder: Embedder) -> None:
        self._conn = conn
        self._embedder = embedder

    @classmethod
    def mount(cls, path: str | Path | None = None) -> Mount:
        """Resolve and prepare a mount, returning a handle for mount-level ops.

        Resolution order: explicit `path` arg > `GRIMOIRE_MOUNT` env var >
        `~/.grimoire`. Creates the mount directory and the shared `models/`
        cache if missing. The manifest TOML is written lazily — on the first
        named-DB create — so a brand-new mount with only a default DB
        carries no manifest file.
        """
        mount_path = _resolve_mount(path)
        _ensure_mount_dirs(mount_path)
        return Mount(mount_path)

    @classmethod
    def create(
        cls,
        name: str | None = None,
        *,
        embedder: Embedder,
        mount: str | Path | None = None,
        description: str | None = None,
        check_same_thread: bool = True,
    ) -> Self:
        """Create a database in the mount and return an open Grimoire.

        `name=None` creates the default at `<mount>/grimoire.db`. A name
        creates `<mount>/<name>/grimoire.db` and registers it in the manifest.
        Raises `DatabaseExists` if a database with this name is already
        present in the mount; use `Grimoire.open` to attach to an existing one.
        """
        if name is not None:
            _validate_name(name)
        mount_path = _resolve_mount(mount)
        _ensure_mount_dirs(mount_path)

        db = _db_path(mount_path, name)
        if db.exists():
            label = "default database" if name is None else f"database {name!r}"
            raise DatabaseExists(f"{label} already exists at {db}")

        # Track the named subdir we create so a failed init leaves nothing
        # behind. The default DB writes directly into the mount root, so
        # there's no per-DB subdir to clean up.
        created_subdir: Path | None = None
        if name is not None:
            subdir = mount_path / name
            created_subdir = subdir if not subdir.exists() else None
            subdir.mkdir(parents=True, exist_ok=True)

        try:
            grimoire = _create_file(
                db, embedder=embedder, check_same_thread=check_same_thread
            )
        except BaseException:
            if created_subdir is not None:
                shutil.rmtree(created_subdir, ignore_errors=True)
            raise

        if name is not None:
            try:
                _register(
                    mount_path, name, model=embedder.model, description=description
                )
            except BaseException:
                grimoire.close()
                if created_subdir is not None:
                    shutil.rmtree(created_subdir, ignore_errors=True)
                raise
        return grimoire

    @classmethod
    def open(
        cls,
        name: str | None = None,
        *,
        mount: str | Path | None = None,
        check_same_thread: bool = True,
    ) -> Self:
        """Open an existing database in the mount.

        `name=None` opens the default at `<mount>/grimoire.db`; a name opens
        `<mount>/<name>/grimoire.db`. The embedder is reconstructed from the
        file's lock row using `FastembedEmbedder` and the shared `<mount>/models/`
        cache — requires the `fastembed` extra. Mount-aware DBs are therefore
        fastembed-bound by contract; custom-embedder workflows must operate
        below this API.
        """
        if name is not None:
            _validate_name(name)
        mount_path = _resolve_mount(mount)
        db = _db_path(mount_path, name)
        if not db.exists():
            label = "default database" if name is None else f"database {name!r}"
            raise GrimoireNotFound(f"No {label} at {db}")
        embedder = _autoload_embedder(db, mount_path)
        return _open_file(db, embedder=embedder, check_same_thread=check_same_thread)

    @classmethod
    def destroy(
        cls,
        name: str | None = None,
        *,
        mount: str | Path | None = None,
    ) -> None:
        """Delete a database from the mount.

        `name=None` removes the default DB; a name removes the named DB and
        its subdirectory and drops the manifest entry. Idempotent: missing
        files or manifest entries are silently tolerated, since the goal
        state is "gone."
        """
        if name is not None:
            _validate_name(name)
        mount_path = _resolve_mount(mount)
        db = _db_path(mount_path, name)

        # Unlink the SQLite file plus its WAL/SHM siblings, in case the file
        # was open elsewhere and the journal hasn't been folded back in.
        for sibling in (
            db,
            db.parent / (db.name + "-wal"),
            db.parent / (db.name + "-shm"),
            db.parent / (db.name + "-journal"),
        ):
            sibling.unlink(missing_ok=True)

        if name is not None:
            subdir = mount_path / name
            # Best-effort: remove the now-empty subdir. Leave it alone if the
            # caller has put other files in there.
            if subdir.exists() and not any(subdir.iterdir()):
                subdir.rmdir()
            _unregister(mount_path, name)

    @classmethod
    def peek(cls, path: str | Path) -> Stats | None:
        """Read metadata and counts from a grimoire file without opening it for use.

        Returns None if the file does not exist or is not a grimoire database.
        Does not load sqlite-vec or require an embedder, so it is safe for
        inspection (CLI `info`, model auto-detect) before deciding how to open.
        """
        path = Path(path)
        if not path.exists():
            return None
        try:
            conn = sqlite3.connect(path)
            try:
                row = conn.execute(
                    "SELECT model, dimension FROM grimoire WHERE id = 1"
                ).fetchone()
                if row is None:
                    return None
                version = conn.execute("PRAGMA user_version").fetchone()[0]
                count = conn.execute("SELECT COUNT(*) FROM entries").fetchone()[0]
                group_key_rows = conn.execute(
                    "SELECT group_key, COUNT(*) FROM entries "
                    "GROUP BY group_key ORDER BY group_key"
                ).fetchall()
            finally:
                conn.close()
        except sqlite3.Error:
            return None
        return Stats(
            model=row[0],
            dimension=row[1],
            schema_version=version,
            entry_count=count,
            groups=dict(group_key_rows),
        )

    def add(
        self,
        *,
        content: str,
        group_key: str | None = None,
        group_ref: str | None = None,
        payload: dict[str, Any] | None = None,
        threshold: float | None = None,
        keywords: list[str] | None = None,
    ) -> Entry:
        entry_id = str(ULID())
        vector = self._embedder.embed(content)
        payload_json = json.dumps(payload) if payload is not None else None
        keywords_json = json.dumps(keywords) if keywords is not None else None
        keywords_text = " ".join(keywords) if keywords else ""

        with self._conn:
            self._conn.execute(
                """
                INSERT INTO entries
                    (id, group_key, group_ref, content, keywords, payload, threshold)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    entry_id,
                    group_key,
                    group_ref,
                    content,
                    keywords_json,
                    payload_json,
                    threshold,
                ),
            )
            self._conn.execute(
                "INSERT INTO vectors (entry_id, group_key, embedding) VALUES (?, ?, ?)",
                (entry_id, group_key, _pack(vector)),
            )
            self._conn.execute(
                "INSERT INTO entries_fts (content, keywords, entry_id) "
                "VALUES (?, ?, ?)",
                (content, keywords_text, entry_id),
            )

        return Entry(
            id=entry_id,
            group_key=group_key,
            group_ref=group_ref,
            content=content,
            payload=payload,
            threshold=threshold,
            keywords=keywords,
        )

    def add_many(self, records: Iterable[Mapping[str, Any]]) -> list[Entry]:
        """Insert many records in one transaction with one batched embed call.

        Each record is a mapping accepting the same keys as `add`'s kwargs:
        `content` is required; `group_key`, `group_ref`, `payload`,
        `threshold`, and `keywords` are optional. Returns the inserted
        entries in input order.

        Atomic: if embedding or any insert fails, nothing is committed —
        unlike a loop over `add`, which would leave partial state behind.
        """
        records = list(records)
        if not records:
            return []

        contents = [r["content"] for r in records]
        vectors = self._embedder.embed_many(contents)

        entries: list[Entry] = []
        entries_rows: list[tuple] = []
        vectors_rows: list[tuple] = []
        fts_rows: list[tuple] = []

        for record, vector in zip(records, vectors, strict=True):
            entry_id = str(ULID())
            group_key = record.get("group_key")
            group_ref = record.get("group_ref")
            content = record["content"]
            payload = record.get("payload")
            threshold = record.get("threshold")
            keywords = record.get("keywords")

            payload_json = json.dumps(payload) if payload is not None else None
            keywords_json = json.dumps(keywords) if keywords is not None else None
            keywords_text = " ".join(keywords) if keywords else ""

            entries_rows.append(
                (
                    entry_id,
                    group_key,
                    group_ref,
                    content,
                    keywords_json,
                    payload_json,
                    threshold,
                )
            )
            vectors_rows.append((entry_id, group_key, _pack(vector)))
            fts_rows.append((content, keywords_text, entry_id))

            entries.append(
                Entry(
                    id=entry_id,
                    group_key=group_key,
                    group_ref=group_ref,
                    content=content,
                    payload=payload,
                    threshold=threshold,
                    keywords=keywords,
                )
            )

        with self._conn:
            self._conn.executemany(
                "INSERT INTO entries "
                "(id, group_key, group_ref, content, keywords, payload, threshold) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                entries_rows,
            )
            self._conn.executemany(
                "INSERT INTO vectors (entry_id, group_key, embedding) VALUES (?, ?, ?)",
                vectors_rows,
            )
            self._conn.executemany(
                "INSERT INTO entries_fts (content, keywords, entry_id) "
                "VALUES (?, ?, ?)",
                fts_rows,
            )

        return entries

    def get(self, entry_id: str) -> Entry | None:
        row = self._conn.execute(
            "SELECT id, group_key, group_ref, content, keywords, payload, threshold "
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
        if group_key is None:
            row = self._conn.execute(
                "SELECT id, group_key, group_ref, content, keywords, "
                "payload, threshold "
                "FROM entries WHERE group_key IS NULL AND group_ref = ?",
                (group_ref,),
            ).fetchone()
        else:
            row = self._conn.execute(
                "SELECT id, group_key, group_ref, content, keywords, "
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
        sql = (
            "SELECT id, group_key, group_ref, content, keywords, payload, threshold "
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
        content: str | None = None,
        group_key: str | None | _Unset = _UNSET,
        group_ref: str | None | _Unset = _UNSET,
        payload: dict[str, Any] | None | _Unset = _UNSET,
        threshold: float | None | _Unset = _UNSET,
        keywords: list[str] | None | _Unset = _UNSET,
    ) -> Entry | None:
        """Patch fields on an entry, leaving omitted fields untouched.

        `content` is non-nullable — pass a value to change it, or omit
        (default `None`) to leave it alone. `group_key`, `group_ref`,
        `payload`, `threshold`, and `keywords` are all nullable — omit to
        leave alone, pass `None` to clear, or pass a new value to replace.

        Returns the updated entry, or `None` if the id is unknown. Raises
        `sqlite3.IntegrityError` if the resulting `(group_key, group_ref)`
        pair would collide with another entry. Re-embeds only when `content`
        changed, re-indexes FTS only when `content` or `keywords` changed,
        and rewrites the vector row only when `content` or `group_key`
        changed.
        """
        current = self.get(entry_id)
        if current is None:
            return None

        new_group_key = (
            current.group_key if isinstance(group_key, _Unset) else group_key
        )
        new_group_ref = (
            current.group_ref if isinstance(group_ref, _Unset) else group_ref
        )
        new_content = current.content if content is None else content
        new_payload = current.payload if isinstance(payload, _Unset) else payload
        new_threshold = (
            current.threshold if isinstance(threshold, _Unset) else threshold
        )
        new_keywords = current.keywords if isinstance(keywords, _Unset) else keywords

        content_changed = new_content != current.content
        keywords_changed = new_keywords != current.keywords
        group_key_changed = new_group_key != current.group_key

        payload_json = json.dumps(new_payload) if new_payload is not None else None
        keywords_json = json.dumps(new_keywords) if new_keywords is not None else None
        keywords_text = " ".join(new_keywords) if new_keywords else ""

        with self._conn:
            self._conn.execute(
                """
                UPDATE entries
                SET group_key = ?, group_ref = ?, content = ?,
                    keywords = ?, payload = ?, threshold = ?
                WHERE id = ?
                """,
                (
                    new_group_key,
                    new_group_ref,
                    new_content,
                    keywords_json,
                    payload_json,
                    new_threshold,
                    entry_id,
                ),
            )

            if content_changed or group_key_changed:
                # vec0 partitions on `group_key`, so a group_key change requires
                # moving the row to a different partition — done as delete +
                # reinsert. When only group_key changed, reuse the stored
                # embedding blob to avoid an unnecessary embedder call.
                if content_changed:
                    new_blob = _pack(self._embedder.embed(new_content))
                else:
                    row = self._conn.execute(
                        "SELECT embedding FROM vectors WHERE entry_id = ?",
                        (entry_id,),
                    ).fetchone()
                    new_blob = row[0]
                self._conn.execute(
                    "DELETE FROM vectors WHERE entry_id = ?", (entry_id,)
                )
                self._conn.execute(
                    "INSERT INTO vectors (entry_id, group_key, embedding) "
                    "VALUES (?, ?, ?)",
                    (entry_id, new_group_key, new_blob),
                )

            if content_changed or keywords_changed:
                self._conn.execute(
                    "DELETE FROM entries_fts WHERE entry_id = ?", (entry_id,)
                )
                self._conn.execute(
                    "INSERT INTO entries_fts (content, keywords, entry_id) "
                    "VALUES (?, ?, ?)",
                    (new_content, keywords_text, entry_id),
                )

        return Entry(
            id=entry_id,
            group_key=new_group_key,
            group_ref=new_group_ref,
            content=new_content,
            payload=new_payload,
            threshold=new_threshold,
            keywords=new_keywords,
        )

    def update_many(self, records: Iterable[Mapping[str, Any]]) -> list[Entry | None]:
        """Patch many entries in one transaction with one batched embed call.

        Each record must include `id`; remaining keys mirror `update`'s
        kwargs (`group_key`, `group_ref`, `content`, `payload`, `threshold`,
        `keywords`). Absent keys leave the field unchanged; passing `None`
        clears nullable fields (`group_key`, `group_ref`, `payload`,
        `threshold`, `keywords`).

        Returns one `Entry | None` per input record in input order — `None`
        when the id is unknown. Atomic: if embedding or any update fails,
        nothing is committed — unlike a loop over `update`, which would
        leave partial state behind. Raises `sqlite3.IntegrityError` if any
        resulting `(group_key, group_ref)` pair would collide.

        Duplicate ids in input raise `ValueError`: which record wins is
        ambiguous, and the returned entries would lie about the post-batch
        state. Callers should dedupe upstream.
        """
        records = list(records)
        if not records:
            return []

        ids = [r["id"] for r in records]
        if len(ids) != len(set(ids)):
            raise ValueError("update_many: duplicate ids in input")

        placeholders = ",".join(["?"] * len(ids))
        rows = self._conn.execute(
            f"SELECT id, group_key, group_ref, content, keywords, payload, threshold "
            f"FROM entries WHERE id IN ({placeholders})",
            ids,
        ).fetchall()
        current_by_id = {row[0]: _row_to_entry(row) for row in rows}

        # First pass: compute target state per record, gather contents to embed.
        targets: list[dict[str, Any] | None] = []
        contents_to_embed: list[str] = []
        embed_indices: list[int] = []
        for i, r in enumerate(records):
            current = current_by_id.get(r["id"])
            if current is None:
                targets.append(None)
                continue
            new_group_key = r.get("group_key", current.group_key)
            new_group_ref = r.get("group_ref", current.group_ref)
            new_content = r.get("content", current.content)
            new_payload = r.get("payload", current.payload)
            new_threshold = r.get("threshold", current.threshold)
            new_keywords = r.get("keywords", current.keywords)
            target = {
                "id": r["id"],
                "group_key": new_group_key,
                "group_ref": new_group_ref,
                "content": new_content,
                "payload": new_payload,
                "threshold": new_threshold,
                "keywords": new_keywords,
                "content_changed": new_content != current.content,
                "group_key_changed": new_group_key != current.group_key,
                "keywords_changed": new_keywords != current.keywords,
            }
            targets.append(target)
            if target["content_changed"]:
                embed_indices.append(i)
                contents_to_embed.append(new_content)

        # One batched embed call covering only records whose content changed.
        blobs: dict[int, bytes] = {}
        if contents_to_embed:
            new_vectors = self._embedder.embed_many(contents_to_embed)
            for idx, vec in zip(embed_indices, new_vectors, strict=True):
                blobs[idx] = _pack(vec)

        results: list[Entry | None] = []
        with self._conn:
            for i, t in enumerate(targets):
                if t is None:
                    results.append(None)
                    continue

                payload_json = (
                    json.dumps(t["payload"]) if t["payload"] is not None else None
                )
                keywords_json = (
                    json.dumps(t["keywords"]) if t["keywords"] is not None else None
                )
                keywords_text = " ".join(t["keywords"]) if t["keywords"] else ""

                self._conn.execute(
                    "UPDATE entries SET group_key = ?, group_ref = ?, "
                    "content = ?, keywords = ?, payload = ?, threshold = ? "
                    "WHERE id = ?",
                    (
                        t["group_key"],
                        t["group_ref"],
                        t["content"],
                        keywords_json,
                        payload_json,
                        t["threshold"],
                        t["id"],
                    ),
                )

                if t["content_changed"] or t["group_key_changed"]:
                    if i in blobs:
                        new_blob = blobs[i]
                    else:
                        # group_key changed but content did not — reuse stored
                        # embedding.
                        row = self._conn.execute(
                            "SELECT embedding FROM vectors WHERE entry_id = ?",
                            (t["id"],),
                        ).fetchone()
                        new_blob = row[0]
                    self._conn.execute(
                        "DELETE FROM vectors WHERE entry_id = ?", (t["id"],)
                    )
                    self._conn.execute(
                        "INSERT INTO vectors (entry_id, group_key, embedding) "
                        "VALUES (?, ?, ?)",
                        (t["id"], t["group_key"], new_blob),
                    )

                if t["content_changed"] or t["keywords_changed"]:
                    self._conn.execute(
                        "DELETE FROM entries_fts WHERE entry_id = ?", (t["id"],)
                    )
                    self._conn.execute(
                        "INSERT INTO entries_fts (content, keywords, entry_id) "
                        "VALUES (?, ?, ?)",
                        (t["content"], keywords_text, t["id"]),
                    )

                results.append(
                    Entry(
                        id=t["id"],
                        group_key=t["group_key"],
                        group_ref=t["group_ref"],
                        content=t["content"],
                        payload=t["payload"],
                        threshold=t["threshold"],
                        keywords=t["keywords"],
                    )
                )
        return results

    def delete(self, entry_id: str) -> bool:
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
        vector = self._embedder.embed(query)

        sql = (
            "SELECT e.id, e.group_key, e.group_ref, e.content, e.keywords, "
            "e.payload, e.threshold, v.distance "
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
                content=r[3],
                keywords=json.loads(r[4]) if r[4] is not None else None,
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
        sql = (
            f"SELECT e.id, e.group_key, e.group_ref, e.content, e.keywords, "
            f"e.payload, e.threshold, {_BM25_RANK} AS rank "
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
        sql += f" ORDER BY {_BM25_RANK} LIMIT ?"
        params.append(k)

        rows = self._conn.execute(sql, params).fetchall()
        return [
            Entry(
                id=r[0],
                group_key=r[1],
                group_ref=r[2],
                content=r[3],
                keywords=json.loads(r[4]) if r[4] is not None else None,
                payload=json.loads(r[5]) if r[5] is not None else None,
                threshold=r[6],
                rank=r[7],
            )
            for r in rows
        ]

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def _create_file(
    path: Path,
    *,
    embedder: Embedder,
    check_same_thread: bool = True,
) -> Grimoire:
    """Create a fresh grimoire file and warm its embedder.

    Module-level rather than a classmethod because `Grimoire.create` is now
    the mount-aware entry point — this is the file-level primitive it
    composes with. Not part of the stable public API; kept reachable so
    tests and advanced callers can build on it.
    """
    is_new = not path.exists()
    if is_new:
        path.parent.mkdir(parents=True, exist_ok=True)
    conn = _open_conn(str(path), check_same_thread=check_same_thread)
    try:
        if is_new:
            create(conn, embedder)
        else:
            validate(conn, embedder)
        embedder.embed(_WARMUP_PROBE)
        return Grimoire(conn=conn, embedder=embedder)
    except BaseException:
        conn.close()
        # WAL mode writes header bytes during `_open_conn`, so a failed
        # init on a brand-new file leaves a non-empty (and useless)
        # SQLite file behind. Clean up the file and its WAL siblings
        # to preserve the "failed init leaves no garbage" invariant.
        if is_new:
            for sibling in (
                path,
                path.parent / (path.name + "-wal"),
                path.parent / (path.name + "-shm"),
                path.parent / (path.name + "-journal"),
            ):
                sibling.unlink(missing_ok=True)
        raise


def _open_file(
    path: Path,
    *,
    embedder: Embedder,
    check_same_thread: bool = True,
) -> Grimoire:
    """Open an existing grimoire file and validate the embedder against it."""
    if not path.exists():
        raise GrimoireNotFound(f"No grimoire at {path}")
    conn = _open_conn(str(path), check_same_thread=check_same_thread)
    try:
        validate(conn, embedder)
        return Grimoire(conn=conn, embedder=embedder)
    except BaseException:
        conn.close()
        raise


def _autoload_embedder(db: Path, mount: Path) -> Embedder:
    """Reconstruct the embedder a file was created with from its lock row.

    Limited to fastembed-bundled models: given the model name, FastembedEmbedder
    can rebuild the embedder. Custom embedders that don't round-trip through a
    string name should be passed explicitly via `Grimoire.open(..., embedder=)`.
    """
    stats = Grimoire.peek(db)
    if stats is None:
        raise GrimoireNotFound(f"Not a grimoire file: {db}")
    try:
        from grimoire.embedders import FastembedEmbedder
    except ImportError as exc:
        raise ImportError(
            "Auto-loading an embedder requires the `fastembed` extra. "
            "Install with: pip install grimoire[fastembed], "
            "or pass embedder= explicitly to Grimoire.open."
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


def _pack(vector: list[float]) -> bytes:
    return struct.pack(f"{len(vector)}f", *vector)


def _ulid_floor(dt: datetime) -> str:
    return str(ULID.from_datetime(dt))[:10] + "0" * 16


def _row_to_entry(row: tuple) -> Entry:
    return Entry(
        id=row[0],
        group_key=row[1],
        group_ref=row[2],
        content=row[3],
        keywords=json.loads(row[4]) if row[4] is not None else None,
        payload=json.loads(row[5]) if row[5] is not None else None,
        threshold=row[6],
    )
