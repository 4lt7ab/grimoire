"""Optional telemetry surface for grimoire.

Mirrors the embedder pattern: callers may pass a `Telemetry` into
`Grimoire.open(...)`. The library wraps each public operation in a span
and emits events at lifecycle moments (embedder lock validated, schema
installed). The default is `NoOpTelemetry`, so existing callers see no
behaviour change.
"""

from __future__ import annotations

import contextlib
import logging
import time
from collections.abc import Iterator
from contextlib import AbstractContextManager
from typing import Any, Protocol


class Telemetry(Protocol):
    """The shape grimoire needs from any telemetry sink.

    Implementations are structural — anything providing `span` and `event`
    with the right signatures satisfies the protocol. Bundled
    implementations live in this module (`NoOpTelemetry`,
    `LoggingTelemetry`); callers can bring their own (e.g. an OTel adapter).
    """

    def span(self, name: str, **attrs: Any) -> AbstractContextManager[None]:
        """Wrap a block of work; the implementation owns timing semantics."""
        ...

    def event(self, name: str, **attrs: Any) -> None:
        """Emit a one-shot occurrence (no enclosing block)."""
        ...


class NoOpTelemetry:
    """Telemetry that drops everything on the floor. The library default."""

    def span(self, name: str, **attrs: Any) -> AbstractContextManager[None]:
        return contextlib.nullcontext()

    def event(self, name: str, **attrs: Any) -> None:
        return None


class LoggingTelemetry:
    """Telemetry that writes via stdlib `logging`.

    Each `span` emits one record on exit with `elapsed_ms` plus the span's
    attrs (INFO on clean exit, ERROR if the body raised). Each `event`
    emits one INFO record with its attrs. Structured fields ride on
    `LogRecord` via `extra={"grimoire": {...}}` — handlers that want JSON
    output can pluck the namespaced sub-dict and avoid collisions with
    built-in `LogRecord` attributes.
    """

    def __init__(self, logger: logging.Logger | None = None) -> None:
        self._logger = logger or logging.getLogger("grimoire")

    @contextlib.contextmanager
    def span(self, name: str, **attrs: Any) -> Iterator[None]:
        start = time.perf_counter()
        try:
            yield
        except BaseException as exc:
            elapsed_ms = (time.perf_counter() - start) * 1000
            self._logger.error(
                "grimoire.span.error %s elapsed_ms=%.2f",
                name,
                elapsed_ms,
                extra={
                    "grimoire": {
                        "name": name,
                        "elapsed_ms": elapsed_ms,
                        "error": type(exc).__name__,
                        **attrs,
                    }
                },
            )
            raise
        else:
            elapsed_ms = (time.perf_counter() - start) * 1000
            self._logger.info(
                "grimoire.span %s elapsed_ms=%.2f",
                name,
                elapsed_ms,
                extra={
                    "grimoire": {
                        "name": name,
                        "elapsed_ms": elapsed_ms,
                        **attrs,
                    }
                },
            )

    def event(self, name: str, **attrs: Any) -> None:
        self._logger.info(
            "grimoire.event %s",
            name,
            extra={"grimoire": {"name": name, **attrs}},
        )
