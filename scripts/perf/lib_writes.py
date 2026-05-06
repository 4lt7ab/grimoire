"""Informal perf pass over Grimoire's write surface.

Resets the db at `.grimoire/grimoire.db`, optionally pre-seeds a corpus
via `add_many`, then exercises add / add_many / update / update_many /
delete / delete_many at a few batch sizes. Output is meant to be eyeballed
at the terminal.

Mode flags (mutex, default = clear db only):
    --full-wipe   Wipe `.grimoire/` entirely — db and model cache. Forces
                  a re-download on the next run. Pytest's next run also
                  redownloads (same cache).
    --keep-data   Don't clear the db. Run on top of existing state. Note:
                  update / delete scenarios mutate live entries.

--corpus N        Seed N records via add_many before scenarios run.
                  Default 0 = no seed. Useful for measuring write perf
                  against a non-empty db (e.g. 100k pre-existing rows).
                  Combine with --keep-data on subsequent runs to skip the
                  re-seed.

Requires the fastembed extra:
    uv sync --package grimoire --extra fastembed

Run from the repo root:
    uv run python scripts/perf/lib_writes.py [--scale N] [--corpus N] \\
        [--full-wipe | --keep-data]
"""

import argparse
import time

from _common import (
    DB,
    MODE_DESCRIPTIONS,
    MODE_KEEP,
    Summary,
    add_wipe_flags,
    fmt,
    mode_from_args,
    prepare_grimoire,
    synth_records,
    time_each,
)
from grimoire import Grimoire


def run(scale: int, mode: str, corpus: int) -> None:
    print(f"# library writes — scale={scale}, corpus={corpus}, mode={mode}")
    print(f"# {MODE_DESCRIPTIONS[mode]}")

    summary = Summary("library writes")

    t0 = time.perf_counter()
    g = prepare_grimoire(mode)
    init_elapsed = time.perf_counter() - t0
    print(f"# init (mkdir + model load + warm probe): {fmt(init_elapsed).strip()}")

    if mode == MODE_KEEP:
        peek = Grimoire.peek(DB)
        initial = peek.entry_count if peek is not None else 0
        print(f"# starting db state: {initial} entries")

    # Optional pre-seed via add_many. Independent of the wipe mode — pairs
    # naturally with `--keep-data` for "seed once, iterate fast" workflows.
    seed_elapsed: float | None = None
    if corpus > 0:
        records = synth_records(corpus, seed=99)
        t0 = time.perf_counter()
        for i in range(0, corpus, 500):
            g.add_many(records[i : i + 500])
        seed_elapsed = time.perf_counter() - t0
        print(f"# seed ({corpus} records via add_many): {fmt(seed_elapsed).strip()}")
    print()

    try:
        # add — single-record latency
        recs = synth_records(scale, seed=10)
        times = time_each(lambda r: g.add(**r), recs)
        summary.each(f"add x {scale}", times, group="add")

        # add_many — throughput at varying batch sizes
        for batch_size in (1, 10, 100, 1000):
            batches = max(1, scale // batch_size)
            recs = synth_records(batches * batch_size, seed=20 + batch_size)
            t0 = time.perf_counter()
            for i in range(batches):
                g.add_many(recs[i * batch_size : (i + 1) * batch_size])
            summary.batch(
                f"add_many batch={batch_size} x {batches}",
                time.perf_counter() - t0,
                batches,
                batch_size,
                group="add_many",
            )

        # update — content-changing path (re-embed + FTS reindex + vec rewrite)
        sample = g.list(limit=scale)
        times = time_each(
            lambda e: g.update(e.id, content=e.content + " (revised)"),
            sample,
        )
        summary.each(
            f"update x {scale} (content → re-embed)", times, group="update (content)"
        )

        # update — kind-only path (no re-embed, vec partition move only)
        sample = g.list(limit=scale)
        new_kinds = ["wraith" if e.kind == "dragon" else "dragon" for e in sample]
        times = time_each(
            lambda pair: g.update(pair[0].id, kind=pair[1]),
            list(zip(sample, new_kinds, strict=True)),
        )
        summary.each(
            f"update x {scale} (kind only → no re-embed)",
            times,
            group="update (kind-only)",
        )

        # update_many — batched throughput on the content-changing path
        for batch_size in (10, 100, 1000):
            sample = g.list(limit=batch_size)
            if not sample:
                continue
            patches = [
                {"id": e.id, "content": e.content + f" v{batch_size}"} for e in sample
            ]
            t0 = time.perf_counter()
            g.update_many(patches)
            summary.batch(
                f"update_many batch={len(patches)} (content)",
                time.perf_counter() - t0,
                1,
                len(patches),
                group="update_many",
            )

        # delete — single-record latency
        sample = g.list(limit=scale)
        ids = [e.id for e in sample]
        times = time_each(g.delete, ids)
        summary.each(f"delete x {len(ids)}", times, group="delete")

        # delete_many — batched throughput
        for batch_size in (10, 100, 1000):
            sample = g.list(limit=batch_size)
            if not sample:
                continue
            del_ids = [e.id for e in sample]
            t0 = time.perf_counter()
            g.delete_many(del_ids)
            summary.batch(
                f"delete_many batch={len(del_ids)}",
                time.perf_counter() - t0,
                1,
                len(del_ids),
                group="delete_many",
            )
    finally:
        g.close()

    # Final state — peek opens its own connection; safe after close.
    extras = {"init": fmt(init_elapsed)}
    if seed_elapsed is not None:
        extras["seed"] = fmt(seed_elapsed)
    peek = Grimoire.peek(DB)
    if peek is not None:
        size_mb = DB.stat().st_size / (1024 * 1024)
        extras["final state"] = f"{peek.entry_count} entries, {size_mb:.2f} MB"
    summary.render(extras=extras)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Informal perf pass over Grimoire's write surface."
    )
    parser.add_argument(
        "--scale",
        type=int,
        default=200,
        help="Sample size for single-call scenarios (default: 200).",
    )
    parser.add_argument(
        "--corpus",
        type=int,
        default=0,
        help=(
            "Seed N records via add_many before scenarios run "
            "(default: 0 = no seed). Use to size the db up before "
            "measuring; pair with --keep-data to skip re-seeding on "
            "subsequent runs."
        ),
    )
    add_wipe_flags(parser)
    args = parser.parse_args()
    run(scale=args.scale, mode=mode_from_args(args), corpus=args.corpus)


if __name__ == "__main__":
    main()
