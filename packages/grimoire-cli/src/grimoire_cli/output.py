"""Output formatting for the grimoire CLI.

Default behavior: pretty (Rich tables / key-value blocks) when stdout is a
terminal, JSONL when piped. Pass `--raw` to force JSONL even at a TTY —
useful when a human wants the raw shape for inspection or piping into a
process that hasn't been started yet.

Every read-side command routes its output through this module so the
TTY/JSONL split lives in exactly one place.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Iterable
from pathlib import Path
from typing import TYPE_CHECKING

from rich.console import Console
from rich.table import Table

if TYPE_CHECKING:
    from grimoire import DbInfo, Entry, Stats


def _is_tty() -> bool:
    """True when stdout is attached to a terminal."""
    try:
        return sys.stdout.isatty()
    except (AttributeError, ValueError):
        return False


def _pretty(*, raw: bool) -> bool:
    """Should we render pretty output? False means JSONL."""
    return not raw and _is_tty()


# ---------- entries (query / search / get / add / update) ----------


def _entry_record(entry: Entry) -> dict[str, object]:
    """Build the JSON shape printed by read commands.

    Includes id and any non-null fields, plus distance/rank when the entry
    came from a search result. Mirrors the on-disk shape minus columns
    that aren't set.
    """
    record: dict[str, object] = {"id": entry.id}
    if entry.vector_text is not None:
        record["vector_text"] = entry.vector_text
    if entry.keyword_text is not None:
        record["keyword_text"] = entry.keyword_text
    if entry.group_key is not None:
        record["group_key"] = entry.group_key
    if entry.group_ref is not None:
        record["group_ref"] = entry.group_ref
    if entry.payload is not None:
        record["payload"] = entry.payload
    if entry.threshold is not None:
        record["threshold"] = entry.threshold
    if entry.distance is not None:
        record["distance"] = entry.distance
    if entry.rank is not None:
        record["rank"] = entry.rank
    return record


def emit_entries(entries: Iterable[Entry], *, raw: bool = False) -> None:
    """Print one row per entry. Pretty table at TTY, JSONL otherwise."""
    entries = list(entries)
    if not _pretty(raw=raw):
        for entry in entries:
            print(json.dumps(_entry_record(entry)))
        return

    if not entries:
        Console().print("[dim](no entries)[/dim]")
        return

    has_group_key = any(e.group_key is not None for e in entries)
    has_group_ref = any(e.group_ref is not None for e in entries)
    has_vector_text = any(e.vector_text is not None for e in entries)
    has_keyword_text = any(e.keyword_text is not None for e in entries)
    has_distance = any(e.distance is not None for e in entries)
    has_rank = any(e.rank is not None for e in entries)

    table = Table(show_lines=False, header_style="bold")
    table.add_column("ID", style="dim", no_wrap=True)
    if has_group_key:
        table.add_column("GROUP", style="cyan", no_wrap=True)
    if has_group_ref:
        table.add_column("REF", style="magenta", no_wrap=True)
    # Single line per entry: snip the text columns with ellipsis instead of
    # wrapping, so a query of 100 results fits in 100 visible rows on screen.
    if has_vector_text:
        table.add_column("VECTOR_TEXT", no_wrap=True, overflow="ellipsis")
    if has_keyword_text:
        table.add_column("KEYWORD_TEXT", no_wrap=True, overflow="ellipsis")
    if has_distance:
        table.add_column("DIST", justify="right", no_wrap=True)
    if has_rank:
        table.add_column("RANK", justify="right", no_wrap=True)

    for entry in entries:
        row: list[str] = [entry.id]
        if has_group_key:
            row.append(entry.group_key or "")
        if has_group_ref:
            row.append(entry.group_ref or "")
        if has_vector_text:
            row.append(entry.vector_text or "")
        if has_keyword_text:
            row.append(entry.keyword_text or "")
        if has_distance:
            row.append(f"{entry.distance:.4f}" if entry.distance is not None else "")
        if has_rank:
            row.append(f"{entry.rank:.4f}" if entry.rank is not None else "")
        table.add_row(*row)

    Console().print(table)


def emit_entry(entry: Entry, *, raw: bool = False) -> None:
    """Print a single entry. Pretty key-value at TTY, JSON otherwise."""
    if not _pretty(raw=raw):
        print(json.dumps(_entry_record(entry)))
        return

    table = Table(show_header=False, box=None, pad_edge=False)
    table.add_column("key", style="cyan", no_wrap=True)
    table.add_column("value")
    table.add_row("id", entry.id)
    if entry.vector_text is not None:
        table.add_row("vector_text", entry.vector_text)
    if entry.keyword_text is not None:
        table.add_row("keyword_text", entry.keyword_text)
    if entry.group_key is not None:
        table.add_row("group_key", entry.group_key)
    if entry.group_ref is not None:
        table.add_row("group_ref", entry.group_ref)
    if entry.payload is not None:
        table.add_row("payload", json.dumps(entry.payload))
    if entry.threshold is not None:
        table.add_row("threshold", f"{entry.threshold:.4f}")
    if entry.distance is not None:
        table.add_row("distance", f"{entry.distance:.4f}")
    if entry.rank is not None:
        table.add_row("rank", f"{entry.rank:.4f}")
    Console().print(table)


# ---------- single DB info (bare grimoire) ----------


def emit_db_info(db_path: Path, stats: Stats, *, raw: bool = False) -> None:
    """Print info for a single database. Pretty key-value at TTY, JSON otherwise."""
    if not _pretty(raw=raw):
        print(
            json.dumps(
                {
                    "path": str(db_path),
                    "model": stats.model,
                    "dimension": stats.dimension,
                    "schema_version": stats.schema_version,
                    "entry_count": stats.entry_count,
                    "groups": stats.groups,
                }
            )
        )
        return

    table = Table(show_header=False, box=None, pad_edge=False)
    table.add_column("key", style="cyan", no_wrap=True)
    table.add_column("value")
    table.add_row("path", str(db_path))
    table.add_row("model", stats.model)
    table.add_row("dimension", str(stats.dimension))
    table.add_row("schema_version", str(stats.schema_version))
    table.add_row("entry_count", str(stats.entry_count))
    if stats.groups:
        groups_str = ", ".join(f"{k}: {v}" for k, v in sorted(stats.groups.items()))
        table.add_row("groups", groups_str)
    else:
        table.add_row("groups", "[dim](none)[/dim]")
    Console().print(table)


# ---------- mount listing (mount / ls) ----------


def emit_listing(infos: Iterable[DbInfo], *, raw: bool = False) -> None:
    """Print one row per database in the mount. Pretty table at TTY, JSONL otherwise."""
    infos = list(infos)
    if not _pretty(raw=raw):
        for info in infos:
            print(
                json.dumps(
                    {
                        "name": info.name,
                        "path": str(info.path),
                        "model": info.model,
                        "dimension": info.dimension,
                        "entry_count": info.entry_count,
                        "is_default": info.is_default,
                    }
                )
            )
        return

    if not infos:
        Console().print("[dim](no databases in mount)[/dim]")
        return

    table = Table(show_lines=False, header_style="bold")
    table.add_column("NAME", style="cyan", no_wrap=True)
    table.add_column("MODEL", no_wrap=True)
    table.add_column("DIM", justify="right", no_wrap=True)
    table.add_column("ENTRIES", justify="right", no_wrap=True)
    table.add_column("DEFAULT", no_wrap=True)
    for info in infos:
        table.add_row(
            info.name if info.name is not None else "[dim](default)[/dim]",
            info.model,
            str(info.dimension),
            str(info.entry_count),
            "✓" if info.is_default else "",
        )
    Console().print(table)
