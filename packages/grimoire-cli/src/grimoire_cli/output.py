"""Output helpers — pretty at a TTY, JSONL otherwise.

Single-record commands stay as plain JSONL: a single line is already
shell-friendly and parses with `jq` either way. Tables are reserved for
multi-record listings where columns help the eye.
"""

from __future__ import annotations

import json
import sys
from typing import Any

import typer
from rich.console import Console
from rich.table import Table

_CONSOLE = Console()


def is_tty() -> bool:
    return sys.stdout.isatty()


def emit_jsonl(rows: list[dict[str, Any]]) -> None:
    for row in rows:
        typer.echo(json.dumps(row))


def emit_kv(rows: list[tuple[str, Any]]) -> None:
    """Pretty key-value lines for single-record summaries."""
    width = max((len(k) for k, _ in rows), default=0)
    for key, value in rows:
        _CONSOLE.print(f"[bold]{key:<{width}}[/bold]  {value}")


def emit_table(headers: list[str], rows: list[list[str]]) -> None:
    table = Table(show_header=True, header_style="bold")
    for h in headers:
        table.add_column(h, overflow="fold")
    for r in rows:
        table.add_row(*r)
    _CONSOLE.print(table)
