from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

import sqlite_vec

from grimoire.data import entry, meta, schema
from grimoire.data.entry import (
    Entry,
    EntryIndex,
    Filters,
    KeywordHit,
    SemanticHit,
)
from grimoire.embed import Embedder, NoOpEmbedder
from grimoire.errors import (
    EmbedderRequired,
    GrimoireMismatch,
    GrimoireNotFound,
)


@dataclass(frozen=True, slots=True)
class Peek:
    """Lightweight snapshot of a grimoire file: lock + per-table row counts."""

    model: str
    dimension: int
    schema_version: int
    entry_count: int
    entry_idx_count: int
    entry_fts_count: int
    entry_vec_count: int


class Grimoire:
    def __init__(
        self,
        conn: sqlite3.Connection,
        embedder: Embedder | None = None,
    ) -> None:
        self._conn = conn
        self.embedder = embedder

    # ------------------------------------------------------------------
    # File lifecycle
    # ------------------------------------------------------------------

    @staticmethod
    def open(
        path: str | Path,
        *,
        embedder: Embedder | None = None,
        check_same_thread: bool = True,
    ) -> Grimoire:
        """Open or initialize a grimoire file at `path`.

        An empty file gets the schema installed and the embedder lock
        written. An initialized file is validated against the supplied
        embedder; mismatched model or dimension raises `GrimoireMismatch`.

        Without an embedder, an empty file locks to NoOp sentinel values
        (model="noop", dimension=1). The lock is sticky: reopening with a
        real embedder later raises `GrimoireMismatch`. Semantic operations
        on a NoOp-locked grimoire raise `EmbedderRequired`.
        """
        conn = sqlite3.connect(path, check_same_thread=check_same_thread)
        conn.row_factory = sqlite3.Row

        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)

        if schema.read_version(conn) == 0:
            lock = embedder if embedder is not None else NoOpEmbedder()
            schema.create(conn, model=lock.model, dimension=lock.dimension)
        else:
            schema.validate(conn)
            if embedder is not None:
                stored_model = meta.fetch(conn, "model")
                stored_dimension = int(meta.fetch(conn, "dimension"))
                if (
                    stored_model != embedder.model
                    or stored_dimension != embedder.dimension
                ):
                    raise GrimoireMismatch(
                        f"Embedder reports model={embedder.model!r}"
                        f" dimension={embedder.dimension},"
                        f" file locked to model={stored_model!r}"
                        f" dimension={stored_dimension}."
                    )

        return Grimoire(conn, embedder=embedder)

    @staticmethod
    def peek(path: str | Path, *, check_same_thread: bool = True) -> Peek:
        """Inspect a grimoire file without binding an embedder.

        Returns model, dimension, schema version, and per-sidecar row
        counts. Raises `GrimoireNotFound` if the file does not exist or
        has not been initialized.
        """
        p = Path(path)
        if not p.exists():
            raise GrimoireNotFound(f"No grimoire at {p}")

        conn = sqlite3.connect(p, check_same_thread=check_same_thread)
        conn.row_factory = sqlite3.Row
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)

        try:
            if schema.read_version(conn) == 0:
                raise GrimoireNotFound(f"{p} is not an initialized grimoire")

            schema.validate(conn)
            model = meta.fetch(conn, "model")
            dimension_str = meta.fetch(conn, "dimension")

            if model is None or dimension_str is None:
                raise GrimoireNotFound(f"{p} is missing its embedder lock")

            counts = {
                t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                for t in ("entry", "entry_idx", "entry_fts", "entry_vec")
            }

            return Peek(
                model=model,
                dimension=int(dimension_str),
                schema_version=schema.read_version(conn),
                entry_count=counts["entry"],
                entry_idx_count=counts["entry_idx"],
                entry_fts_count=counts["entry_fts"],
                entry_vec_count=counts["entry_vec"],
            )
        finally:
            conn.close()

    def __enter__(self) -> Grimoire:
        return self

    def __exit__(self, exc_type, *_) -> None:
        if exc_type is None:
            self._conn.commit()
        else:
            self._conn.rollback()

    # ------------------------------------------------------------------
    # entry  (identity table)
    # ------------------------------------------------------------------

    def add(self, entries: list[Entry]) -> list[Entry]:
        """Insert entries with freshly-minted ULIDs."""
        return entry.add(self._conn, entries)

    def update(self, entries: list[Entry]) -> list[Entry]:
        """Rewrite the `data` column on existing rows, keyed by `uniq_id`."""
        return entry.update(self._conn, entries)

    def remove(self, uniq_ids: list[str]) -> list[str]:
        """Delete entries. Sidecar rows are cascade-cleaned by DB trigger."""
        return entry.remove(self._conn, uniq_ids)

    def get(self, uniq_ids: list[str]) -> list[Entry]:
        """Fetch entries by uniq_id. Returns only the ones that exist."""
        return entry.get(self._conn, uniq_ids)

    def fetch(
        self, uniq_refs: list[str]
    ) -> tuple[list[Entry], list[EntryIndex]]:
        """Fetch entries whose entry_idx row has uniq_ref in the given list.

        Returns parallel `(entries, indexes)` lists. Entries without an
        entry_idx row are excluded. Multiple entries may share a uniq_ref
        (no uniqueness constraint), so the result may contain more rows
        than refs were passed.
        """
        return entry.fetch_by_uniq_ref(self._conn, uniq_refs)

    # ------------------------------------------------------------------
    # entry_idx  (filterable metadata sidecar)
    # ------------------------------------------------------------------

    def query(
        self,
        filters: Filters | None = None,
        limit: int = 100,
        cursor: str | None = None,
    ) -> tuple[list[Entry], list[EntryIndex]]:
        """Walk entry_idx rows ordered by `uniq_id` ASC.

        Returns parallel `(entries, indexes)` lists. `cursor`, if given,
        returns rows with `uniq_id > cursor`. For ordinal-window paging,
        pass `Filters(gte={...}, lte={...})`.
        """
        return entry.fetch_idx(self._conn, filters, limit, cursor=cursor)

    # ------------------------------------------------------------------
    # entry_fts  (FTS5 keyword sidecar)
    # ------------------------------------------------------------------

    def match(
        self,
        query: str,
        filters: Filters | None = None,
        limit: int | None = None,
    ) -> tuple[list[Entry], list[KeywordHit]]:
        """FTS5 BM25 search. Filters apply via JOIN to entry_idx.

        Returns parallel `(entries, hits)` lists in BM25 rank order.
        """
        return entry.keyword_search(self._conn, query, filters, limit)

    # ------------------------------------------------------------------
    # entry_vec  (vec0 semantic sidecar)
    # ------------------------------------------------------------------

    def search(
        self, query: str, limit: int = 10
    ) -> tuple[list[Entry], list[SemanticHit]]:
        """Embed `query` via the bound embedder and run vec0 KNN.

        Returns parallel `(entries, hits)` lists, nearest-first by distance.
        """
        if self.embedder is None:
            raise EmbedderRequired(
                "This grimoire was opened without an embedder; "
                "pass embedder=... to Grimoire.open() to enable semantic search."
            )
        return entry.semantic_search(
            self._conn, self.embedder.embed(query), limit
        )

    # ------------------------------------------------------------------
    # Combined PUT-style indexing
    # ------------------------------------------------------------------

    def index(
        self,
        uniq_id: str,
        *,
        ref: str | None = None,
        ord: tuple[float | None, float | None, float | None] | None = None,
        nom: tuple[str | None, str | None] | None = None,
        match: str | None = None,
        search: str | None = None,
    ) -> None:
        """One-shot index across the three sidecars for a single entry, PUT-style.

        Each kwarg writes wholesale; no reads, no merging.

        - `ref`, `ord`, `nom` together describe the `entry_idx` row. If any
          one of the three is supplied, the row is fully replaced from the
          given kwargs; columns mapped to unsupplied or in-tuple `None`
          positions become NULL. If all three are omitted, `entry_idx` is
          untouched. A 3-tuple is expected for `ord`, a 2-tuple for `nom`.
        - `match` replaces the `entry_fts` row.
        - `search` embeds the text via the bound embedder and replaces the
          `entry_vec` row. Raises `EmbedderRequired` if the grimoire was
          opened without one.

        The entry referenced by `uniq_id` must already exist in `entry`;
        otherwise the underlying sidecar writes raise `ValueError`.
        """
        if ord is not None and len(ord) != 3:
            raise ValueError("`ord` must be a 3-tuple")
        if nom is not None and len(nom) != 2:
            raise ValueError("`nom` must be a 2-tuple")

        if ref is not None or ord is not None or nom is not None:
            entry.entry_idx_set(
                self._conn,
                [
                    EntryIndex(
                        uniq_id=uniq_id,
                        uniq_ref=ref,
                        nominal_1=nom[0] if nom else None,
                        nominal_2=nom[1] if nom else None,
                        ordinal_1=ord[0] if ord else None,
                        ordinal_2=ord[1] if ord else None,
                        ordinal_3=ord[2] if ord else None,
                    )
                ],
            )

        if match is not None:
            entry.keyword(self._conn, [(uniq_id, match)])

        if search is not None:
            if self.embedder is None:
                raise EmbedderRequired(
                    "This grimoire was opened without an embedder; "
                    "pass embedder=... to Grimoire.open() to enable "
                    "semantic indexing."
                )
            vec = self.embedder.embed(search)
            entry.embed(self._conn, [(uniq_id, search, vec)])
