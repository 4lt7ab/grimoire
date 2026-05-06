"""Shared helpers for the informal perf scripts.

Each script resets the database under `.grimoire/`, re-initializes (via
the library surface or by spawning `grimoire init`), and prints
wall-clock timings. Nothing here is meant to be a benchmark suite — it's
eyeball-grade telemetry for "did that just get slower?"

Layout — `.grimoire/` holds everything:
  - `.grimoire/grimoire.db` — SQLite file
  - `.grimoire/models/` — embedder cache. The CLI's natural location;
    library perf scripts and pytest also point at this path so there's
    one cache on disk for the whole repo.
  - `.grimoire/perf-tmp/` — JSONL / id-list scratch files for the CLI
    scripts' `ingest`, `update-many`, `delete-many` scenarios

Three modes, selected by the script's CLI flags:

- `reset` (default): clear the db file and its `-wal`/`-shm`/`-journal`
  siblings. Cache untouched. Subsequent runs are fast.
- `full` (`--full-wipe`): clear `.grimoire/` entirely — db, cache, and
  scratch files. Forces a cold-start re-download. Pytest's next run
  also redownloads (same cache).
- `keep` (`--keep-data`): clear nothing. Run scenarios on top of existing
  state. Useful for sizing tests against a large corpus; risky on writes
  scripts because update/delete scenarios mutate live data.
"""

import argparse
import json
import random
import shutil
import statistics
import subprocess
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from grimoire import Grimoire
from grimoire.embedders import FastembedEmbedder

REPO_ROOT = Path(__file__).resolve().parents[2]
MOUNT = REPO_ROOT / ".grimoire"
DB = MOUNT / "grimoire.db"
# CLI's natural location; pytest's `_shared_models_cache` fixture and
# `just init` also point here so the whole repo runs off one cache.
MODELS = MOUNT / "models"

# SQLite leaves up to four files behind for one logical database; the
# default reset globs all of them so a stale `-wal` doesn't smuggle state
# across runs.
_DB_SIBLINGS = (
    DB,
    DB.with_suffix(".db-wal"),
    DB.with_suffix(".db-shm"),
    DB.with_suffix(".db-journal"),
)

# Mode strings — `prepare_grimoire` accepts these; scripts translate
# argparse flags into them.
MODE_RESET = "reset"
MODE_FULL = "full"
MODE_KEEP = "keep"
MODES = (MODE_RESET, MODE_FULL, MODE_KEEP)
MODE_DESCRIPTIONS = {
    MODE_RESET: "db cleared, model cache kept",
    MODE_FULL: "db and model cache cleared (forces re-download)",
    MODE_KEEP: "nothing cleared — running on existing data",
}

KINDS = (
    "dragon",
    "phoenix",
    "wraith",
    "kraken",
    "basilisk",
    "chimera",
    "hydra",
    "sphinx",
)
ACTIONS = (
    "slumbers",
    "hunts",
    "guards",
    "haunts",
    "shapes",
    "consumes",
    "broods over",
    "patrols",
)
PLACES = (
    "underworld",
    "crystal caves",
    "sunken city",
    "frostlands",
    "scorching dunes",
    "iron tower",
    "ruins",
)
WHENS = (
    "century",
    "blood moon",
    "eclipse",
    "winter solstice",
    "thousand years",
)
TEMPLATES = (
    "A {kind} that {action} beneath the {place}.",
    "Legends say the {kind} {action} once a {when}.",
    "When the {when} comes, the {kind} {action} the {place}.",
    "A weary {kind} {action} amid the {place}.",
    "Old wizards claim a {kind} can {action} the {place}.",
)


def wipe_db() -> None:
    """Delete the SQLite db file and any auxiliary siblings (`-wal`, etc)."""
    for path in _DB_SIBLINGS:
        path.unlink(missing_ok=True)


def wipe_mount() -> None:
    """Delete `.grimoire/` entirely — db, model cache, scratch files."""
    if MOUNT.exists():
        shutil.rmtree(MOUNT)


def prepare_grimoire(mode: str) -> Grimoire:
    """Apply the wipe mode and return an open Grimoire.

    `Grimoire.init` is idempotent — it creates the file when absent and
    validates it when present — so the same call works for all three modes.
    """
    if mode == MODE_FULL:
        wipe_mount()
    elif mode == MODE_RESET:
        wipe_db()
    elif mode == MODE_KEEP:
        pass
    else:
        raise ValueError(f"unknown mode: {mode!r} (expected one of {MODES})")

    MOUNT.mkdir(parents=True, exist_ok=True)
    MODELS.mkdir(parents=True, exist_ok=True)
    embedder = FastembedEmbedder(cache_folder=MODELS)
    return Grimoire.init(DB, embedder=embedder)


def add_wipe_flags(parser: argparse.ArgumentParser) -> None:
    """Attach `--full-wipe` and `--keep-data` to a parser as a mutex group."""
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--full-wipe",
        action="store_true",
        help="Also wipe the model cache. Forces a cold-start re-download.",
    )
    group.add_argument(
        "--keep-data",
        action="store_true",
        help=(
            "Don't reset the db; run scenarios on top of existing state. "
            "Note: write-side scenarios still mutate (update/delete) entries."
        ),
    )


def mode_from_args(args: argparse.Namespace) -> str:
    """Resolve the parsed flags into a mode string."""
    if args.full_wipe:
        return MODE_FULL
    if args.keep_data:
        return MODE_KEEP
    return MODE_RESET


# ----- CLI subprocess helpers ------------------------------------------
#
# CLI scripts spawn `grimoire <cmd>` per scenario step. Each invocation
# pays Python startup + embedder load on top of the operation itself, so
# the wall-clock numbers from `cli_run` mostly tell that startup story.

CLI_TMP_DIR = MOUNT / "perf-tmp"

_GRIMOIRE_BIN: str | None = None


def grimoire_bin() -> str:
    """Resolve the `grimoire` executable. Cached after first call.

    Relies on the venv being on PATH — invoke perf scripts via
    `uv run python scripts/perf/...` so this resolves correctly.
    """
    global _GRIMOIRE_BIN
    if _GRIMOIRE_BIN is None:
        path = shutil.which("grimoire")
        if path is None:
            raise RuntimeError(
                "grimoire CLI not found on PATH. Install with: "
                "uv sync --package grimoire-cli --extra fastembed"
            )
        _GRIMOIRE_BIN = path
    return _GRIMOIRE_BIN


def cli_run(
    args: list[str],
    *,
    input_text: str | None = None,
) -> tuple[float, subprocess.CompletedProcess]:
    """Run `grimoire <args> --mount .grimoire/`, time it, return (elapsed, result).

    Captures stdout and stderr; the timing covers process spawn, Python
    startup, embedder load, the operation, and process exit — i.e. the
    real wall-clock cost a CLI user pays.
    """
    cmd = [grimoire_bin(), *args, "--mount", str(MOUNT)]
    t0 = time.perf_counter()
    result = subprocess.run(
        cmd,
        input=input_text,
        capture_output=True,
        text=True,
        check=True,
    )
    return time.perf_counter() - t0, result


def prepare_cli_mount(mode: str) -> None:
    """Apply the wipe mode for CLI scripts.

    Unlike `prepare_grimoire`, this returns nothing — CLI scripts open no
    library handle; the next step is calling `cli_run(["init"])`. The CLI
    creates `<mount>/models/` itself on first use (= `.grimoire/models/`,
    the same path the library scripts and pytest use).
    """
    if mode == MODE_FULL:
        wipe_mount()
    elif mode == MODE_RESET:
        wipe_db()
        # Sweep the scratch dir so `ingest` etc. don't read stale files
        # from a prior run. Model cache survives.
        if CLI_TMP_DIR.exists():
            shutil.rmtree(CLI_TMP_DIR)
    elif mode == MODE_KEEP:
        pass
    else:
        raise ValueError(f"unknown mode: {mode!r} (expected one of {MODES})")

    MOUNT.mkdir(parents=True, exist_ok=True)
    CLI_TMP_DIR.mkdir(parents=True, exist_ok=True)


def write_jsonl(path: Path, records: Iterable[dict[str, Any]]) -> None:
    """Write records to a JSONL file, one JSON object per line."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for record in records:
            f.write(json.dumps(record))
            f.write("\n")


def write_ids_file(path: Path, ids: Iterable[str]) -> None:
    """Write one entry id per line — the format `delete-many` accepts."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for entry_id in ids:
            f.write(entry_id)
            f.write("\n")


def parse_jsonl_output(stdout: str) -> list[dict[str, Any]]:
    """Parse the JSONL stdout from a read-side CLI call (`list`, `get`, etc)."""
    return [json.loads(line) for line in stdout.splitlines() if line.strip()]


def synth_records(n: int, *, seed: int = 0) -> list[dict[str, Any]]:
    """Deterministic synthetic records with kind variety.

    The trailing `[#seed-i]` marker keeps content unique across scenarios
    so consecutive `add` calls don't accidentally collide on the embedder
    side (and so `update` rewrites are visible).
    """
    rng = random.Random(seed)
    records: list[dict[str, Any]] = []
    for i in range(n):
        kind = KINDS[i % len(KINDS)]
        template = rng.choice(TEMPLATES)
        content = (
            template.format(
                kind=kind,
                action=rng.choice(ACTIONS),
                place=rng.choice(PLACES),
                when=rng.choice(WHENS),
            )
            + f" [#{seed}-{i}]"
        )
        records.append({"kind": kind, "content": content})
    return records


def time_each(fn: Callable[[Any], Any], items: Iterable[Any]) -> list[float]:
    """Run `fn(item)` for each item, returning per-call elapsed seconds."""
    times: list[float] = []
    for item in items:
        t0 = time.perf_counter()
        fn(item)
        times.append(time.perf_counter() - t0)
    return times


def time_n(fn: Callable[[], Any], n: int) -> list[float]:
    """Run a no-arg `fn` n times, returning per-call elapsed seconds."""
    times: list[float] = []
    for _ in range(n):
        t0 = time.perf_counter()
        fn()
        times.append(time.perf_counter() - t0)
    return times


def fmt(seconds: float) -> str:
    """Format an elapsed duration with a unit appropriate to its size.

    Always returns 8 chars so values right-align cleanly in columnar output.
    """
    if seconds >= 1:
        return f"{seconds:6.2f}s "
    if seconds >= 1e-3:
        return f"{seconds * 1000:6.1f}ms"
    return f"{seconds * 1e6:6.0f}us"


# Detail-table layout. Both row types share the same column grid; cells
# that don't apply to a row type are filled with an em-dash so the columns
# always line up.
LABEL_W = 50
COL_W = 8
COLS = ("total", "median", "p90", "per-rec", "per-bat")
DASH = f"{'—':>{COL_W}}"


def print_table_header() -> None:
    """Print the header row for the detail table. Call once before scenarios."""
    cols = "  ".join(f"{c:>{COL_W}}" for c in COLS)
    print(f"{'scenario':<{LABEL_W}}  {cols}")


def _print_row(label: str, cells: list[str]) -> None:
    print(f"{label:<{LABEL_W}}  {'  '.join(cells)}")


def report_each(label: str, times: list[float]) -> None:
    """Print a single-call row: total, median, p90 (per-rec/per-bat = —)."""
    total = fmt(sum(times))
    median = fmt(statistics.median(times))
    p90 = fmt(sorted(times)[max(0, int(0.9 * (len(times) - 1)))])
    _print_row(label, [total, median, p90, DASH, DASH])


def report_batch(label: str, elapsed: float, batches: int, batch_size: int) -> None:
    """Print a batched-call row: total, per-rec, per-bat (median/p90 = —)."""
    records = batches * batch_size
    per_record = elapsed / records if records else 0.0
    per_batch = elapsed / batches if batches else 0.0
    _print_row(label, [fmt(elapsed), DASH, DASH, fmt(per_record), fmt(per_batch)])


@dataclass
class _Row:
    group: str
    label: str
    metric: float  # seconds — median for `each`, per-record for `batch`


@dataclass
class Summary:
    """Collect a headline metric per scenario for an end-of-run digest.

    Wraps `report_each` and `report_batch`, auto-printing the table header
    on the first call. On `render()`, prints wall time, any extras, and a
    per-group `n / fastest / slowest` table so regressions in one method
    family are obvious without re-reading the whole log.
    """

    name: str
    start: float = field(default_factory=time.perf_counter)
    rows: list[_Row] = field(default_factory=list)
    _header_printed: bool = field(default=False, init=False, repr=False)

    def _ensure_header(self) -> None:
        if not self._header_printed:
            print_table_header()
            self._header_printed = True

    def each(self, label: str, times: list[float], *, group: str) -> None:
        """Print a `report_each` row and record its median for the digest."""
        self._ensure_header()
        report_each(label, times)
        self.rows.append(_Row(group, label, statistics.median(times)))

    def batch(
        self,
        label: str,
        elapsed: float,
        batches: int,
        batch_size: int,
        *,
        group: str,
    ) -> None:
        """Print a `report_batch` row and record its per-record cost."""
        self._ensure_header()
        report_batch(label, elapsed, batches, batch_size)
        records = batches * batch_size
        per_record = elapsed / records if records else 0.0
        self.rows.append(_Row(group, label, per_record))

    def render(self, *, extras: dict[str, str] | None = None) -> None:
        elapsed = time.perf_counter() - self.start
        info = {"wall time": fmt(elapsed), **(extras or {})}

        print()
        print(f"# {self.name} — summary")
        print()
        info_w = max(len(k) for k in info)
        for k, v in info.items():
            # `fmt` right-pads to 8 chars; lstrip so numeric values left-anchor
            # at the same column as free-form text values like "30 entries…".
            print(f"  {k:<{info_w}}  {v.lstrip()}")

        if not self.rows:
            return

        groups: dict[str, list[_Row]] = {}
        for row in self.rows:
            groups.setdefault(row.group, []).append(row)

        group_w = max(len("group"), max(len(g) for g in groups))
        print()
        print(
            f"  {'group':<{group_w}}  {'n':>3}  "
            f"{'fastest':>{COL_W}}  {'slowest':>{COL_W}}"
        )
        for group, items in groups.items():
            metrics = [r.metric for r in items]
            lo, hi = min(metrics), max(metrics)
            print(
                f"  {group:<{group_w}}  {len(items):>3}  "
                f"{fmt(lo):>{COL_W}}  {fmt(hi):>{COL_W}}"
            )
