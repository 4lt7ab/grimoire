"""Async facade over `Grimoire`.

A thread-offload wrapper, not native async I/O: each call runs the synchronous
`Grimoire` method on a worker thread via `asyncio.to_thread`, so a grimoire can
live inside an event loop without blocking it. Throughput matches the sync
library — the win is not stalling the loop.

A single lock serializes access to the underlying SQLite connection, matching
SQLite's one-writer model and keeping the thread-affine connection safe under
the `check_same_thread=False` it is opened with.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path
from typing import Any, TypeVar

from grimoire.data.entry import (
    Entry,
    EntryIndex,
    Filters,
    KeywordHit,
    SemanticHit,
)
from grimoire.embed import Embedder
from grimoire.grimoire import Grimoire, Peek
from grimoire.telemetry import Telemetry

T = TypeVar("T")


class AsyncGrimoire:
    def __init__(self, inner: Grimoire) -> None:
        self._inner = inner
        self._lock = asyncio.Lock()

    @property
    def embedder(self) -> Embedder | None:
        return self._inner.embedder

    @staticmethod
    async def open(
        path: str | Path,
        *,
        embedder: Embedder | None = None,
        telemetry: Telemetry | None = None,
    ) -> AsyncGrimoire:
        inner = await asyncio.to_thread(
            Grimoire.open,
            path,
            embedder=embedder,
            telemetry=telemetry,
            check_same_thread=False,
        )
        return AsyncGrimoire(inner)

    @staticmethod
    async def peek(path: str | Path) -> Peek:
        return await asyncio.to_thread(Grimoire.peek, path, check_same_thread=False)

    async def __aenter__(self) -> AsyncGrimoire:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: object,
    ) -> None:
        await self._run(self._inner.__exit__, exc_type, exc, tb)

    async def _run(self, fn: Callable[..., T], *args: Any, **kwargs: Any) -> T:
        async with self._lock:
            return await asyncio.to_thread(fn, *args, **kwargs)

    async def analyze(self) -> None:
        await self._run(self._inner.analyze)

    async def add(self, entries: list[Entry]) -> list[Entry]:
        return await self._run(self._inner.add, entries)

    async def update(self, entries: list[Entry]) -> list[Entry]:
        return await self._run(self._inner.update, entries)

    async def remove(self, uniq_ids: list[str]) -> list[str]:
        return await self._run(self._inner.remove, uniq_ids)

    async def get(self, uniq_ids: list[str]) -> list[Entry]:
        return await self._run(self._inner.get, uniq_ids)

    async def fetch(
        self, uniq_refs: list[str]
    ) -> tuple[list[Entry], list[EntryIndex]]:
        return await self._run(self._inner.fetch, uniq_refs)

    async def query(
        self,
        filters: Filters | None = None,
        limit: int = 100,
        cursor: str | None = None,
        ascending: bool = True,
    ) -> tuple[list[Entry], list[EntryIndex]]:
        return await self._run(self._inner.query, filters, limit, cursor, ascending)

    async def match(
        self,
        query: str,
        filters: Filters | None = None,
        limit: int = 10,
    ) -> tuple[list[Entry], list[KeywordHit]]:
        return await self._run(self._inner.match, query, filters, limit)

    async def search(
        self, query: str, limit: int = 10
    ) -> tuple[list[Entry], list[SemanticHit]]:
        return await self._run(self._inner.search, query, limit)

    async def index(
        self,
        uniq_id: str,
        *,
        ref: str | None = None,
        group: str | None = None,
        ord: tuple[Any, Any, Any, Any, Any] | None = None,
        match: str | None = None,
        search: str | None = None,
    ) -> None:
        await self._run(
            lambda: self._inner.index(
                uniq_id, ref=ref, group=group, ord=ord, match=match, search=search
            )
        )
